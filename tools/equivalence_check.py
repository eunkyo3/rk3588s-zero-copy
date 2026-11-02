#!/usr/bin/env python3
"""Equivalence check of PROTOCOL §11.1 and §11.2 (M1-4), under the criteria of AMENDMENTS A5.

Reads the output of tools/equivalence_dump for B, C and R: a JSONL file per preprocessing (a header
line, then one line per frame with its detections) and the input tensors its header names. R is
B's preprocessing with plain INTER_LINEAR; in A5 it takes C's place against B.

Tensor level (§11.1), over the letterbox band: the mean absolute error over all frames and elements,
and the minimum per-frame PSNR (peak 255). Every border element of every tensor must be 114.

Detection level (§11.2): scores and coordinates are rounded to float32 when read, the type
post-processing gives them. Per frame and class, boxes are matched with the Hungarian algorithm on IoU.
IoU below 0.5 counts as 0, so the matching maximises the summed IoU of the pairs with IoU >= 0.5,
the only pairs formed. A box whose confidence is within 0.25 +- 0.02 (the edges as float32), or that
is matched to such a box, is left out of the counts. A frame agrees when the
counted boxes of every class are equally many.

Registered criteria (§11.1, §11.2), reported: MAE <= 2, minimum PSNR >= 35 dB, the border check;
every matched pair with IoU >= 0.95 and confidence difference <= 0.02, and at least 99% of frames
agreeing.
Criteria in force (A5), which set the exit code: the border check for B and C; C's MAE at most R's
and C's minimum PSNR at least R's; (a) the share of matched pairs within both thresholds and (b) the
share of agreeing frames at least R's; (c) |N_C - N_B| / N_B <= 0.02, N counting every box
post-processing returned, over all frames and classes.

The registered input is 1,000 frames and a 640x360 band (§11); other inputs are checked the same
way, and the report says that they are not the registered input.

Exit code: 0 if the criteria in force hold, 1 if they do not, 2 if the inputs are inconsistent or the
check could not run.

Usage: tools/equivalence_check.py --b B.jsonl --c C.jsonl --r R.jsonl [--frames 1000] [--json OUT]
"""
import argparse
import bisect
import json
import math
import statistics
import struct
import sys
import traceback
from collections import Counter
from pathlib import Path

PAD = 114
MATCH_IOU = 0.5
PAIR_IOU = 0.95
PAIR_DCONF = 0.02
MAE_MAX = 2.0
PSNR_MIN = 35.0
FRAMES_AGREE_MIN = 0.99
TOTAL_TOLERANCE = 0.02
CLASS_REPORT_MIN = 100  # per-class box counts are shown for classes with at least this many B boxes
REGISTERED_FRAMES = 1000
REGISTERED_BAND = (640, 360)


def f32(x):
    return struct.unpack("f", struct.pack("f", x))[0]


NEAR_LO, NEAR_HI = f32(0.25 - 0.02), f32(0.25 + 0.02)

# Histogram bins: IoU left-closed ([0.95, 0.98) ...), |dconf| right-closed with 0 on its own.
IOU_EDGES = [0.5, 0.8, 0.9, 0.95, 0.98, 0.99]
IOU_LABELS = ["[0.50, 0.80)", "[0.80, 0.90)", "[0.90, 0.95)", "[0.95, 0.98)", "[0.98, 0.99)", "[0.99, 1.00]"]
DCONF_EDGES = [0.0, 0.01, 0.02, 0.05, 0.1, 0.2, math.inf]
DCONF_LABELS = ["0", "(0, 0.01]", "(0.01, 0.02]", "(0.02, 0.05]", "(0.05, 0.10]", "(0.10, 0.20]", "> 0.20"]


class InputError(Exception):
    pass


# --- matching ---------------------------------------------------------------------------------


def assignment(weights):
    """Maximum-weight assignment for a matrix (list of rows) of non-negative weights.

    Returns {row: column}. With rows <= columns every row is assigned, otherwise every column.
    Hungarian algorithm with potentials (shortest augmenting paths), O(n^2 m)."""
    n = len(weights)
    m = len(weights[0]) if n else 0
    if n == 0 or m == 0:
        return {}
    transposed = n > m
    if transposed:
        weights = [list(col) for col in zip(*weights)]
        n, m = m, n
    inf = math.inf
    u = [0.0] * (n + 1)
    v = [0.0] * (m + 1)
    p = [0] * (m + 1)  # p[j]: row assigned to column j, 1-based, 0 for none
    way = [0] * (m + 1)
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [inf] * (m + 1)
        used = [False] * (m + 1)
        while True:
            used[j0] = True
            i0, delta, j1 = p[j0], inf, 0
            row = weights[i0 - 1]
            for j in range(1, m + 1):
                if not used[j]:
                    cur = -row[j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j], way[j] = cur, j0
                    if minv[j] < delta:
                        delta, j1 = minv[j], j
            for j in range(m + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while j0:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
    result = {}
    for j in range(1, m + 1):
        if p[j]:
            if transposed:
                result[j - 1] = p[j] - 1
            else:
                result[p[j] - 1] = j - 1
    return result


def iou(a, b):
    """IoU of two detections [class, score, x1, y1, x2, y2]."""
    w = max(0.0, min(a[4], b[4]) - max(a[2], b[2]))
    h = max(0.0, min(a[5], b[5]) - max(a[3], b[3]))
    inter = w * h
    union = (a[4] - a[2]) * (a[5] - a[3]) + (b[4] - b[2]) * (b[5] - b[3]) - inter
    return inter / union if union > 0 else 0.0


def match(side, base):
    """Matched pairs (i, j, IoU) between the detections of one frame, per class."""
    pairs = []
    for cls in sorted({d[0] for d in side} & {d[0] for d in base}):
        si = [i for i, d in enumerate(side) if d[0] == cls]
        bj = [j for j, d in enumerate(base) if d[0] == cls]
        ious = [[iou(side[i], base[j]) for j in bj] for i in si]
        masked = [[x if x >= MATCH_IOU else 0.0 for x in row] for row in ious]
        for r, c in sorted(assignment(masked).items()):
            if ious[r][c] >= MATCH_IOU:
                pairs.append((si[r], bj[c], ious[r][c]))
    return pairs


def near_threshold(d):
    return NEAR_LO <= d[1] <= NEAR_HI


def frame_agrees(side, base, pairs):
    """Whether the counted boxes of every class are equally many (§11.2)."""
    near_s = {i for i, d in enumerate(side) if near_threshold(d)}
    near_b = {j for j, d in enumerate(base) if near_threshold(d)}
    out_s, out_b = set(near_s), set(near_b)
    for i, j, _ in pairs:
        if i in near_s or j in near_b:
            out_s.add(i)
            out_b.add(j)
    counts = Counter(d[0] for i, d in enumerate(side) if i not in out_s)
    counts.subtract(d[0] for j, d in enumerate(base) if j not in out_b)
    return all(v == 0 for v in counts.values())


def detection_stats(side, base):
    """Pair, frame and histogram statistics of `side` against `base`, both lists of frames."""
    iou_hist = [0] * len(IOU_LABELS)
    dconf_hist = [0] * len(DCONF_LABELS)
    pairs = iou_ok = dconf_ok = both_ok = agree = 0
    min_iou, max_dconf = math.inf, 0.0
    for s, b in zip(side, base):
        frame_pairs = match(s, b)
        for i, j, v in frame_pairs:
            dc = abs(s[i][1] - b[j][1])
            pairs += 1
            iou_ok += v >= PAIR_IOU
            dconf_ok += dc <= PAIR_DCONF
            both_ok += v >= PAIR_IOU and dc <= PAIR_DCONF
            min_iou, max_dconf = min(min_iou, v), max(max_dconf, dc)
            iou_hist[bisect.bisect_right(IOU_EDGES, v) - 1] += 1
            dconf_hist[0 if dc == 0 else bisect.bisect_left(DCONF_EDGES, dc)] += 1
        agree += frame_agrees(s, b, frame_pairs)
    return {
        "pairs": pairs,
        "iou_ok": iou_ok,
        "dconf_ok": dconf_ok,
        "both_ok": both_ok,
        "both_ok_share": share(both_ok, pairs),
        "min_iou": min_iou if pairs else None,
        "max_dconf": max_dconf,
        "frames": len(side),
        "frames_agreeing": agree,
        "frames_agreeing_share": share(agree, len(side)),
        "iou_histogram": dict(zip(IOU_LABELS, iou_hist)),
        "dconf_histogram": dict(zip(DCONF_LABELS, dconf_hist)),
    }


# --- tensors ----------------------------------------------------------------------------------


def tensor_stats(paths, header, n):
    """MAE and per-frame PSNR of C and R against B over the band, and the border check."""
    import numpy as np

    h, w = header["model_h"], header["model_w"]
    x, y, bw, bh = header["band"]
    t = {}
    for k, p in paths.items():
        size = Path(p).stat().st_size
        if size != n * h * w * 3:
            raise InputError(f"{p}: {size} bytes, expected {n} tensors of {h}x{w}x3")
        t[k] = np.memmap(p, dtype=np.uint8, mode="r", shape=(n, h, w, 3))
    elements = bh * bw * 3
    out = {k: {"abs_sum": 0, "signed_sum": [0, 0, 0], "psnr": []} for k in ("C", "R")}
    border_ok = {k: True for k in t}
    for f in range(n):
        for k, a in t.items():
            img = a[f]
            border_ok[k] &= bool(
                (img[:y] == PAD).all() and (img[y + bh:] == PAD).all()
                and (img[y:y + bh, :x] == PAD).all() and (img[y:y + bh, x + bw:] == PAD).all())
        ref = t["B"][f, y:y + bh, x:x + bw].astype(np.int32)
        for k in ("C", "R"):
            d = t[k][f, y:y + bh, x:x + bw].astype(np.int32) - ref
            s = out[k]
            s["abs_sum"] += int(np.abs(d).sum())
            s["signed_sum"] = [a + int(b) for a, b in zip(s["signed_sum"], d.sum(axis=(0, 1)))]
            sq = int((d * d).sum())
            s["psnr"].append(math.inf if sq == 0 else 10 * math.log10(255.0**2 * elements / sq))
    result = {"border_114": border_ok, "elements_per_frame": elements}
    for k, s in out.items():
        psnr = s["psnr"]
        worst = min(range(n), key=psnr.__getitem__)
        result[k] = {
            "mae": s["abs_sum"] / (elements * n),
            "min_psnr_db": psnr[worst],
            "min_psnr_frame": worst,
            "median_psnr_db": statistics.median(psnr),
            "mean_diff_rgb": [v / (elements // 3 * n) for v in s["signed_sum"]],
        }
    return result


# --- inputs -----------------------------------------------------------------------------------


def load(path, variant):
    """(header, frames) from a JSONL file; frames is a list of (frame, pts, detections)."""
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    if not lines:
        raise InputError(f"{path}: empty")
    first = json.loads(lines[0])
    header = first.get("header") if isinstance(first, dict) else None
    if not isinstance(header, dict):
        raise InputError(f"{path}: the first line is not a header")
    if header.get("variant") != variant or header.get("run_kind") != "equivalence":
        raise InputError(f"{path}: variant {header.get('variant')}, run kind {header.get('run_kind')}; "
                         f"expected {variant}, equivalence")
    frames = []
    for line in lines[1:]:
        rec = json.loads(line)
        frames.append((rec["frame"], rec["pts"], [[d[0]] + [f32(v) for v in d[1:]] for d in rec["detections"]]))
    return header, frames


def check_inputs(headers, frames, expected):
    same = ("model_sha256", "source", "code_commit", "amendment", "model_w", "model_h", "band", "labels")
    for key in same:
        values = {json.dumps(h.get(key)) for h in headers.values()}
        if len(values) != 1:
            raise InputError(f"header field {key} differs between the files: {sorted(values)}")
    keys = {k: [(f, pts) for f, pts, _ in fr] for k, fr in frames.items()}
    if not keys["B"] == keys["C"] == keys["R"]:
        raise InputError("the files do not hold the same frames (index and pts)")
    if [f for f, _ in keys["B"]] != list(range(len(keys["B"]))):
        raise InputError("frame indices are not 0, 1, 2, ...")
    if len(keys["B"]) != expected:
        raise InputError(f"{len(keys['B'])} frames, expected {expected}")
    classes = len(headers["B"]["labels"])
    for k, fr in frames.items():
        if any(len(d) != 6 or not 0 <= d[0] < classes for _, _, dets in fr for d in dets):
            raise InputError(f"{k}: a detection is not [class, score, x1, y1, x2, y2] with a known class")
    if not any(dets for _, _, dets in frames["B"]):
        raise InputError("B has no detections")


# --- report -----------------------------------------------------------------------------------


def pct(x):
    return f"{100 * x:.2f}%"


def share(a, b):
    return a / b if b else 0.0


def verdict(ok):
    return "pass" if ok else "FAIL"


def finite(x):
    if isinstance(x, float) and not math.isfinite(x):
        return None
    if isinstance(x, dict):
        return {k: finite(v) for k, v in x.items()}
    if isinstance(x, list):
        return [finite(v) for v in x]
    return x


def run(args):
    paths = {"B": args.b, "C": args.c, "R": args.r}
    headers, frames = {}, {}
    for k, p in paths.items():
        headers[k], frames[k] = load(p, k)
    check_inputs(headers, frames, args.frames)
    hb = headers["B"]
    n = len(frames["B"])
    labels = hb["labels"]
    dets = {k: [d for _, _, d in fr] for k, fr in frames.items()}
    tensors = {k: str(Path(p).parent / headers[k]["tensors"]) for k, p in paths.items()}

    ts = tensor_stats(tensors, hb, n)
    ds = {k: detection_stats(dets[k], dets["B"]) for k in ("C", "R")}
    totals = {k: sum(len(d) for d in dets[k]) for k in dets}
    per_class = {k: Counter(b[0] for d in dets[k] for b in d) for k in dets}

    border = ts["border_114"]["B"] and ts["border_114"]["C"]
    reg_tensor = ts["C"]["mae"] <= MAE_MAX and ts["C"]["min_psnr_db"] >= PSNR_MIN and border
    reg_det = (ds["C"]["both_ok"] == ds["C"]["pairs"]
               and ds["C"]["frames_agreeing_share"] >= FRAMES_AGREE_MIN)
    total_dev = (totals["C"] - totals["B"]) / totals["B"]
    a5 = {
        "border_114_b_and_c": border,
        "tensor_mae": ts["C"]["mae"] <= ts["R"]["mae"],
        "tensor_min_psnr": ts["C"]["min_psnr_db"] >= ts["R"]["min_psnr_db"],
        "a_pairs_within_both_thresholds": ds["C"]["both_ok_share"] >= ds["R"]["both_ok_share"],
        "b_frames_agreeing": ds["C"]["frames_agreeing_share"] >= ds["R"]["frames_agreeing_share"],
        "c_total_boxes": abs(total_dev) <= TOTAL_TOLERANCE,
    }
    passed = all(a5.values())

    x, y, bw, bh = hb["band"]
    registered_input = n == REGISTERED_FRAMES and (bw, bh) == REGISTERED_BAND
    out = [
        f"inputs: {n} frames of {hb['source']}" + ("" if registered_input else
                                                     f" (not the registered input: {REGISTERED_FRAMES} frames, "
                                                     f"a {REGISTERED_BAND[0]}x{REGISTERED_BAND[1]} band)"),
        f"  model {hb['model_sha256']}, code {hb['code_commit']}, amendment {hb['amendment']}",
        f"  B: {hb['preprocessing']}; C: {headers['C']['preprocessing']}; R: {headers['R']['preprocessing']}",
        f"tensor level, band {bw}x{bh} at ({x}, {y}):",
    ]
    for k, name in (("C", "C vs B"), ("R", "R vs B")):
        s = ts[k]
        out.append(f"  {name}: MAE {s['mae']:.4f}, min PSNR {s['min_psnr_db']:.2f} dB (frame "
                   f"{s['min_psnr_frame']}), median PSNR {s['median_psnr_db']:.2f} dB, mean difference RGB "
                   + ", ".join(f"{v:+.3f}" for v in s["mean_diff_rgb"]))
    out.append("  border 114: " + ", ".join(f"{k} {'yes' if v else 'NO'}" for k, v in ts["border_114"].items()))
    out.append("detection level, Hungarian per class, pairs at IoU >= 0.5:")
    for k, name in (("C", "C vs B"), ("R", "R vs B")):
        s = ds[k]
        out.append(f"  {name}: {s['pairs']} pairs; IoU >= 0.95 {pct(share(s['iou_ok'], s['pairs']))}, |dconf| <= 0.02 "
                   f"{pct(share(s['dconf_ok'], s['pairs']))}, both {pct(s['both_ok_share'])}; min IoU {s['min_iou'] or 0:.3f}, "
                   f"max |dconf| {s['max_dconf']:.3f}; frames agreeing {s['frames_agreeing']}/{n} "
                   f"({pct(s['frames_agreeing_share'])})")
    for title, key, labels_ in (("IoU", "iou_histogram", IOU_LABELS), ("|dconf|", "dconf_histogram", DCONF_LABELS)):
        out.append(f"  {title} of matched pairs:        C vs B    R vs B")
        for lab in labels_:
            c, r = ds["C"][key][lab], ds["R"][key][lab]
            out.append(f"    {lab:<14} {c:7d} {pct(share(c, ds['C']['pairs'])):>8} {r:7d} "
                       f"{pct(share(r, ds['R']['pairs'])):>8}")
    out.append(f"boxes, every box post-processing returned (classes with at least {CLASS_REPORT_MIN} B boxes):")
    for cls, nb in sorted(per_class["B"].items(), key=lambda kv: -kv[1]):
        if nb < CLASS_REPORT_MIN:
            continue
        nc, nr = per_class["C"][cls], per_class["R"][cls]
        out.append(f"  {labels[cls]:<14} B {nb:6d}  C {nc:6d} ({(nc - nb) / nb:+8.2%})  R {nr:6d} ({(nr - nb) / nb:+8.2%})")
    out.append(f"  {'all classes':<14} B {totals['B']:6d}  C {totals['C']:6d} ({total_dev:+8.2%})  R {totals['R']:6d} "
               f"({(totals['R'] - totals['B']) / totals['B']:+8.2%})")
    out += [
        "registered criteria (PROTOCOL §11.1, §11.2), reported:",
        f"  tensor: MAE {ts['C']['mae']:.4f} <= {MAE_MAX:g} {verdict(ts['C']['mae'] <= MAE_MAX)}, min PSNR "
        f"{ts['C']['min_psnr_db']:.2f} dB >= {PSNR_MIN:g} {verdict(ts['C']['min_psnr_db'] >= PSNR_MIN)}, border "
        f"{verdict(border)}: {verdict(reg_tensor)}",
        f"  detection: all {ds['C']['pairs']} pairs within both thresholds ({ds['C']['both_ok']} are) "
        f"{verdict(ds['C']['both_ok'] == ds['C']['pairs'])}, frames agreeing {pct(ds['C']['frames_agreeing_share'])} "
        f">= 99% {verdict(ds['C']['frames_agreeing_share'] >= FRAMES_AGREE_MIN)}: {verdict(reg_det)}",
        "criteria in force (AMENDMENTS A5), which set the exit code:",
        f"  border 114 in every B and C tensor: {verdict(a5['border_114_b_and_c'])}",
        f"  tensor MAE, C {ts['C']['mae']:.4f} <= R {ts['R']['mae']:.4f}: {verdict(a5['tensor_mae'])}",
        f"  tensor min PSNR, C {ts['C']['min_psnr_db']:.2f} dB >= R {ts['R']['min_psnr_db']:.2f} dB: "
        f"{verdict(a5['tensor_min_psnr'])}",
        f"  (a) pairs within both thresholds, C {pct(ds['C']['both_ok_share'])} >= R {pct(ds['R']['both_ok_share'])}: "
        f"{verdict(a5['a_pairs_within_both_thresholds'])}",
        f"  (b) frames agreeing, C {pct(ds['C']['frames_agreeing_share'])} >= R "
        f"{pct(ds['R']['frames_agreeing_share'])}: {verdict(a5['b_frames_agreeing'])}",
        f"  (c) total boxes, |{totals['C']} - {totals['B']}| / {totals['B']} = {abs(total_dev):.2%} <= 2%: "
        f"{verdict(a5['c_total_boxes'])}",
        f"verdict (A5): {'PASS' if passed else 'FAIL'}" + ("" if registered_input else ", not on the registered input"),
    ]
    # The JSON first: if writing it fails, no report with a verdict is printed.
    if args.json:
        summary = {
            "frames": n, "registered_input": registered_input, "headers": headers, "tensor": ts, "detection": ds,
            "boxes_total": totals,
            "boxes_per_class": {k: {labels[c]: v for c, v in sorted(cnt.items())} for k, cnt in per_class.items()},
            "registered": {"tensor": reg_tensor, "detection": reg_det}, "a5": a5, "pass": passed,
        }
        Path(args.json).write_text(json.dumps(finite(summary), indent=1) + "\n", encoding="utf-8")
    print("\n".join(out))
    return 0 if passed else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--b", required=True, help="B's JSONL from tools/equivalence_dump")
    ap.add_argument("--c", required=True, help="C's JSONL")
    ap.add_argument("--r", required=True, help="R's JSONL (B with INTER_LINEAR, AMENDMENTS A5)")
    ap.add_argument("--frames", type=int, default=1000, help="expected frame count (PROTOCOL §11: 1000)")
    ap.add_argument("--json", help="also write the numbers to this file")
    args = ap.parse_args(argv)
    try:
        return run(args)
    except (InputError, OSError, ValueError, KeyError) as e:
        print(f"equivalence_check: {e}", file=sys.stderr)
        return 2
    except Exception:  # a crash is not a verdict: keep 1 for "criteria not met"
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    sys.exit(main())
