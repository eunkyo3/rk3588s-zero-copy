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

Run kind m1.1, the 4K tensors of AMENDMENTS A11, is checked the same way, but no criteria are in
force: A5's criteria are reported and nothing is judged. It is not the registered input, the JSON's
"pass" is null, and the exit code is 0 once the check has run.

Pooled (the held-out check of AMENDMENTS A8): with --set DIR FRAMES once per source, two or more,
DIR holding b.jsonl, c.jsonl and r.jsonl from one tools/equivalence_dump run, the frames of all
sources are taken together in the order given, and the criteria apply to that pool. Each source's own
results are reported as well; they do not set the exit code. The JSON then has a list of headers, one
per source, and a "sources" list.

Exit code: 0 if the criteria in force hold, 1 if they do not, 2 if the inputs are inconsistent or the
check could not run.

Usage: tools/equivalence_check.py --b B.jsonl --c C.jsonl --r R.jsonl [--frames 1000] [--json OUT]
       tools/equivalence_check.py --set DIR FRAMES [--set DIR FRAMES ...] [--json OUT]
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
RUN_KINDS = ("equivalence", "m1.1")
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


def tensor_sums(paths, header, n):
    """One source's sums of C and R against B over the band, its per-frame PSNRs and its border check."""
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
    return {"frames": n, "border_114": border_ok, "elements_per_frame": elements, **out}


def tensor_stats(parts):
    """MAE and per-frame PSNR of C and R against B over the band, and the border check, over the
    frames of every part (tensor_sums of one source each, in order)."""
    elements = parts[0]["elements_per_frame"]
    n = sum(p["frames"] for p in parts)
    result = {"border_114": {k: all(p["border_114"][k] for p in parts) for k in parts[0]["border_114"]},
              "elements_per_frame": elements}
    for k in ("C", "R"):
        psnr = [(v, i, f) for i, p in enumerate(parts) for f, v in enumerate(p[k]["psnr"])]
        worst = min(psnr, key=lambda t: t[0])  # the first of equal minima
        result[k] = {
            "mae": sum(p[k]["abs_sum"] for p in parts) / (elements * n),
            "min_psnr_db": worst[0],
            "min_psnr_frame": worst[2],
            "median_psnr_db": statistics.median(v for v, _, _ in psnr),
            "mean_diff_rgb": [sum(p[k]["signed_sum"][c] for p in parts) / (elements // 3 * n) for c in range(3)],
        }
        if len(parts) > 1:
            result[k]["min_psnr_source"] = parts[worst[1]]["source"]
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
    if header.get("variant") != variant or header.get("run_kind") not in RUN_KINDS:
        raise InputError(f"{path}: variant {header.get('variant')}, run kind {header.get('run_kind')}; "
                         f"expected {variant}, {' or '.join(RUN_KINDS)}")
    frames = []
    for line in lines[1:]:
        rec = json.loads(line)
        frames.append((rec["frame"], rec["pts"], [[d[0]] + [f32(v) for v in d[1:]] for d in rec["detections"]]))
    return header, frames


def check_inputs(headers, frames, expected):
    same = ("run_kind", "model_sha256", "source", "code_commit", "amendment", "model_w", "model_h", "band", "labels")
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


def check_sets(headers):
    """Sources pooled together share the model, decoder, code, registration, preprocessing and geometry,
    and differ in source."""
    for key in ("run_kind", "model_sha256", "decoder", "code_commit", "amendment", "prereg_v1_commit",
                "freeze_m1_commit", "model_w", "model_h", "band", "labels"):
        values = {json.dumps(h["B"].get(key)) for h in headers}
        if len(values) != 1:
            raise InputError(f"header field {key} differs between the sources: {sorted(values)}")
    for k in ("B", "C", "R"):
        values = {json.dumps(h[k].get("preprocessing")) for h in headers}
        if len(values) != 1:
            raise InputError(f"{k}'s preprocessing differs between the sources: {sorted(values)}")
    sources = [h["B"].get("source") for h in headers]
    if len(set(sources)) != len(sources):
        raise InputError(f"a source is given twice: {sources}")


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


def evaluate(sets):
    """The statistics and the A5 conditions over the frames of `sets`: (headers, frames, tensor_sums)
    per source, pooled in order."""
    dets = {k: [d for _, fr, _ in sets for _, _, d in fr[k]] for k in ("B", "C", "R")}
    ts = tensor_stats([part for _, _, part in sets])
    ds = {k: detection_stats(dets[k], dets["B"]) for k in ("C", "R")}
    totals = {k: sum(len(d) for d in dets[k]) for k in dets}
    border = ts["border_114"]["B"] and ts["border_114"]["C"]
    total_dev = (totals["C"] - totals["B"]) / totals["B"]
    a5 = {
        "border_114_b_and_c": border,
        "tensor_mae": ts["C"]["mae"] <= ts["R"]["mae"],
        "tensor_min_psnr": ts["C"]["min_psnr_db"] >= ts["R"]["min_psnr_db"],
        "a_pairs_within_both_thresholds": ds["C"]["both_ok_share"] >= ds["R"]["both_ok_share"],
        "b_frames_agreeing": ds["C"]["frames_agreeing_share"] >= ds["R"]["frames_agreeing_share"],
        "c_total_boxes": abs(total_dev) <= TOTAL_TOLERANCE,
    }
    return {"dets": dets, "tensor": ts, "detection": ds, "totals": totals, "border": border,
            "total_dev": total_dev, "a5": a5, "pass": all(a5.values())}


def run(args):
    if args.set:
        groups = [({k: str(Path(d) / f"{k.lower()}.jsonl") for k in ("B", "C", "R")}, frames)
                  for d, frames in args.set]
    else:
        groups = [({"B": args.b, "C": args.c, "R": args.r}, args.frames)]
    loaded = []  # every JSONL is checked before any tensor is read
    for paths, expected in groups:
        headers, frames = {}, {}
        for k, p in paths.items():
            headers[k], frames[k] = load(p, k)
        check_inputs(headers, frames, expected)
        loaded.append((paths, headers, frames))
    check_sets([h for _, h, _ in loaded])
    sets = []
    for paths, headers, frames in loaded:
        tensors = {k: str(Path(p).parent / headers[k]["tensors"]) for k, p in paths.items()}
        part = tensor_sums(tensors, headers["B"], len(frames["B"]))
        part["source"] = headers["B"]["source"]
        sets.append((headers, frames, part))
    pooled = len(sets) > 1
    headers = sets[0][0]
    hb = headers["B"]
    labels = hb["labels"]

    ev = evaluate(sets)
    ts, ds, totals, a5, passed = ev["tensor"], ev["detection"], ev["totals"], ev["a5"], ev["pass"]
    border, total_dev = ev["border"], ev["total_dev"]
    n = len(ev["dets"]["B"])
    per_class = {k: Counter(b[0] for d in ev["dets"][k] for b in d) for k in ev["dets"]}
    reg_tensor = ts["C"]["mae"] <= MAE_MAX and ts["C"]["min_psnr_db"] >= PSNR_MIN and border
    reg_det = (ds["C"]["both_ok"] == ds["C"]["pairs"]
               and ds["C"]["frames_agreeing_share"] >= FRAMES_AGREE_MIN)
    per_source = [(h["B"]["source"], len(fr["B"]), evaluate([(h, fr, part)])) for h, fr, part in sets] if pooled else []

    x, y, bw, bh = hb["band"]
    m1_1 = hb["run_kind"] == "m1.1"
    registered_input = not pooled and not m1_1 and n == REGISTERED_FRAMES and (bw, bh) == REGISTERED_BAND
    not_registered = (" (run kind m1.1, AMENDMENTS A11: not the registered input; reported, not judged)" if m1_1
                      else f" (not the registered input: {REGISTERED_FRAMES} frames, "
                           f"a {REGISTERED_BAND[0]}x{REGISTERED_BAND[1]} band)")
    if pooled:
        out = [f"inputs: {n} frames pooled over {len(sets)} sources" + not_registered]
        out += [f"  {source}: {frames} frames" for source, frames, _ in per_source]
    else:
        out = [f"inputs: {n} frames of {hb['source']}" + ("" if registered_input else not_registered)]
    out += [
        f"  model {hb['model_sha256']}, code {hb['code_commit']}, amendment {hb['amendment']}",
        f"  B: {hb['preprocessing']}; C: {headers['C']['preprocessing']}; R: {headers['R']['preprocessing']}",
        f"tensor level, band {bw}x{bh} at ({x}, {y}):",
    ]
    for k, name in (("C", "C vs B"), ("R", "R vs B")):
        s = ts[k]
        out.append(f"  {name}: MAE {s['mae']:.4f}, min PSNR {s['min_psnr_db']:.2f} dB (frame "
                   f"{s['min_psnr_frame']}{' of ' + s['min_psnr_source'] if pooled else ''}), median PSNR {s['median_psnr_db']:.2f} dB, mean difference RGB "
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
        "A5's criteria, reported (run kind m1.1, AMENDMENTS A11: not in force):" if m1_1
        else "criteria in force (AMENDMENTS A5), which set the exit code:",
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
    ]
    if pooled:
        out.append("per source, reported (the verdict is on the pool):")
        for source, frames, e in per_source:
            t, d = e["tensor"], e["detection"]
            out.append(f"  {source}, {frames} frames: MAE C {t['C']['mae']:.4f} / R {t['R']['mae']:.4f}; min PSNR "
                       f"C {t['C']['min_psnr_db']:.2f} / R {t['R']['min_psnr_db']:.2f} dB; (a) C "
                       f"{pct(d['C']['both_ok_share'])} / R {pct(d['R']['both_ok_share'])}; (b) C "
                       f"{pct(d['C']['frames_agreeing_share'])} / R {pct(d['R']['frames_agreeing_share'])}; (c) "
                       f"{e['total_dev']:+.2%}; border {'yes' if e['border'] else 'NO'}: "
                       f"{'PASS' if e['pass'] else 'FAIL'}")
    if m1_1:
        out.append(f"A5's criteria: {'all hold' if passed else 'not all hold'}; run kind m1.1, reported, "
                   "not judged (AMENDMENTS A11)")
    else:
        out.append(f"verdict (A5): {'PASS' if passed else 'FAIL'}"
                   + (f", pooled over {len(sets)} sources" if pooled else "")
                   + ("" if registered_input else ", not on the registered input"))
    # The JSON first: if writing it fails, no report with a verdict is printed.
    if args.json:
        summary = {
            "frames": n, "registered_input": registered_input,
            "headers": [h for h, _, _ in sets] if pooled else headers, "tensor": ts, "detection": ds,
            "boxes_total": totals,
            "boxes_per_class": {k: {labels[c]: v for c, v in sorted(cnt.items())} for k, cnt in per_class.items()},
            "registered": {"tensor": reg_tensor, "detection": reg_det}, "a5": a5, "pass": None if m1_1 else passed,
        }
        if pooled:
            summary["sources"] = [{"source": source, "frames": frames, "tensor": e["tensor"],
                                   "detection": e["detection"], "boxes_total": e["totals"], "a5": e["a5"],
                                   "pass": e["pass"]} for source, frames, e in per_source]
        Path(args.json).write_text(json.dumps(finite(summary), indent=1) + "\n", encoding="utf-8")
    print("\n".join(out))
    return 0 if passed or m1_1 else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--b", help="B's JSONL from tools/equivalence_dump")
    ap.add_argument("--c", help="C's JSONL")
    ap.add_argument("--r", help="R's JSONL (B with INTER_LINEAR, AMENDMENTS A5)")
    ap.add_argument("--frames", type=int, help="expected frame count (default and PROTOCOL §11: 1000)")
    ap.add_argument("--set", nargs=2, action="append", metavar=("DIR", "FRAMES"),
                    help="a source to pool (AMENDMENTS A8): DIR holds b.jsonl, c.jsonl and r.jsonl; "
                         "FRAMES is its expected frame count. Give it once per source.")
    ap.add_argument("--json", help="also write the numbers to this file")
    args = ap.parse_args(argv)
    if args.set:
        if args.b or args.c or args.r or args.frames is not None:
            ap.error("--set replaces --b, --c, --r and --frames")
        if len(args.set) < 2:
            ap.error("--set pools sources: give it at least twice (one source: --b, --c, --r)")
        try:
            args.set = [(d, int(n)) for d, n in args.set]
        except ValueError:
            ap.error("FRAMES of --set must be an integer")
    elif not (args.b and args.c and args.r):
        ap.error("give --b, --c and --r, or --set once per source")
    elif args.frames is None:
        args.frames = REGISTERED_FRAMES
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
