#!/usr/bin/env python3
"""M4's report tables (prereg/M4.md §6), from its records under results/m4/. M4 has no hypotheses: nothing
here is a verdict.

  - Accuracy (§2, §6): for each model and precision, COCOeval's AP@[.5:.95], AP50, AP75, AP small, medium
    and large and AR@100 over the 5,000 images (the INT8 passes' map/ detections, the FP32 passes'); INT8 −
    FP32; AP@[.5:.95] and AP50 of every evaluation also without the 20 quantization images; the pipeline's
    settings (§2.4, the INT8 passes' pipeline/ detections) in a table of their own; the published values,
    marked as obtained under other settings, and §5's trigger; per evaluation, the detections per image, the
    images that reached 300 and how many of those were tied; per pass, its record and checks (§2.4, §2.5).
  - NPU capacity (§3.1): from the calib_npu session the pipeline sessions took (their saturation.txt), P_cap
    and the raw rate per model with the rknn_run, bind and post-processing times and the NPU load; the
    saturation labels (§3.2); M1-3's YOLOv8n values for reference.
  - Pipeline (§3.2, §3.3): per point and arm, the mean ± sample sd of M2 §3.2's metrics and of each stage's
    p50 and p99 (tools/m1_stages.py); for each metric, the paired differences C10_r − C8_r, their mean and a
    descriptive 95% t interval (2 degrees of freedom), and for process and system CPU also
    (C10_r − C8_r) / C8_r; PROTOCOL §9.3's flag for a CV of process CPU above 10%; the runs that do not count.
  - M4's C8 next to M2's Cp at the same points (exploratory: the same arm, another binary and session).

Usage, from the repository: python3 tools/m4_report.py [--out FILE]   (Markdown)
"""
import argparse
import csv
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import m1_report as rep  # noqa: E402
import m1_stages as stg  # noqa: E402
import m2_report as m2r  # noqa: E402
import m4_order as om  # noqa: E402
import stream_session as ss  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
MODELS = (("yolov8n", "YOLOv8n"), ("yolov10n", "YOLOv10n"))
STATS = (("ap", "AP@[.5:.95]"), ("ap50", "AP50"), ("ap75", "AP75"), ("ap_small", "AP small"),
         ("ap_medium", "AP medium"), ("ap_large", "AP large"), ("ar100", "AR@100"))
PUBLISHED = {"yolov8n": 37.3, "yolov10n": 38.5}  # §5: AP@[.5:.95], other settings
TRIGGER_POINTS = 2.0  # §5: FP32 AP@[.5:.95], rounded to 0.1, more than this from its published value
POINTS = {"m4_main": ("main", 8, 5), "m4_aux": ("aux", 5, 30)}  # §3.2: sweep -> (point, channels, fps)
ARMS = ("C8", "C10")
ARM_MODEL = {"C8": "yolov8n", "C10": "yolov10n"}
SATURATION_SHARE = 0.8  # §3.2, PROTOCOL §6.2
M1_CALIB = REPO / "results/calibration/20261102T073918Z_npu/calibration.csv"  # M1-3 (A7), M1's yolov8_post
M2_SWEEPS = {"m4_main": "m2_main", "m4_aux": "m2_aux"}  # the same points in M2
METRICS = (  # (key, label, digits, relative): M2 §3.2's metrics; relative adds (C10 − C8) / C8
    ("process_cpu_s", "Process CPU (s)", 3, True),
    ("process_cpu_a76_s", "Process CPU, A76 (s)", 3, False),
    ("process_cpu_a55_s", "Process CPU, A55 (s)", 3, False),
    ("process_cpu_unattributed_s", "Process CPU, unattributed (s)", 3, False),
    ("schedstat_total_minus_idle_s", "System CPU, /proc/schedstat, minus idle (s)", 3, True),
    ("system_cpu_total_minus_idle_s", "System CPU, /proc/stat, minus idle (s)", 3, True),
    ("latency_p50_ms", "Latency p50 (ms)", 2, False),
    ("latency_p99_ms", "Latency p99 (ms)", 2, False),
    ("drop_rate", "Drop rate", 5, False),
    ("inference_fps_min", "Inference rate, lowest channel (fps)", 3, False),
    ("inference_fps_mean", "Inference rate, channel mean (fps)", 3, False),
    ("phase_spread_ms", "Arrival phases' spread (ms)", 1, False),
    ("stagger_error_ms", "Stagger error (ms)", 1, False),
)
STAGE_METRICS = tuple((f"stage_{s}_{q}_ms", f"Stage {s}, {q} (ms)", 3, False)
                      for s, _, _ in stg.STAGES if s != "total" for q in ("p50", "p99"))  # total: the latency rows
MAIN_ONLY = ("phase_spread_ms", "stagger_error_ms")  # §3.2: at the main point
GROUP_METRICS = (  # exploratory: CPU by thread group, threads.csv, with tools/cpu_breakdown.py's groups
    ("group_npu_s", "NPU workers (`npu-*`) (s)", 3, False),
    ("group_dec_s", "Decoder (`dec-*`, `mpp_*`) (s)", 3, False),
    ("group_pre_s", "Preprocessing (`pre-*`) (s)", 3, False),
    ("group_rest_s", "The rest (`bench_main`, `metrics`) (s)", 3, False),
)


def fmt(x, digits=3):
    return "–" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.{digits}f}"


def signed(x, digits=3):
    return "–" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:+.{digits}f}"


def pct(x, digits=2):
    return "–" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{100 * x:+.{digits}f}%"


def key_values(path):
    """({header key: value}, {key: value}) of a "# key: value" header and a key,value table."""
    header, lines = {}, []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.startswith("# "):
            k, _, v = line[2:].partition(": ")
            header[k] = v
        else:
            lines.append(line)
    return header, {r[0]: r[1] for r in csv.reader(lines) if len(r) == 2}


def freeze_m4(root):
    rows = ss.registration_rows((root / "docs/notes/env.md").read_text(encoding="utf-8"))
    return rows["freeze-m4"][0]


# ---------------------------------------------------------------- accuracy

def passes(root, commit):
    """{(model, precision): [attempts in start order]} (tools/m4_order.py), each with its pass.csv."""
    out = {}
    for a in om.pass_attempts(root / "results/m4", commit):
        h, r = key_values(a.record / "pass.csv") if (a.record / "pass.csv").exists() else ({}, {})
        out.setdefault((a.model, a.precision), []).append({"record": a.record.name, "outcome": a.outcome,
                                                           "header": h, "rows": r})
    return out


def evaluations(root, commit):
    """{(model, precision, "map"|"pipeline"): evaluation} of the runs of tools/m4_coco.py of kind m4 that
    exited 0, keyed by the pass record they evaluated. Several evaluations of one pass's detections are an
    error: which one counts would be a choice."""
    out = {}
    for d in sorted((root / "results/m4").iterdir()):
        if not (d / "coco_eval.csv").exists():
            continue
        h, r = key_values(d / "coco_eval.csv")
        ended = om.keyed(d / "exit.txt")
        if h.get("run_kind") != "m4" or h.get("freeze_m4_commit") != commit or ended.get("exit_code") != "0":
            continue
        subset = "pipeline" if h.get("pass", "").endswith("_pipeline") else "map"
        key = (h["model"], h["precision"], subset)
        if key in out:
            raise SystemExit(f"m4_report: two evaluations of {key}: {out[key]['record']} and {d.name}")
        out[key] = {"record": d.name, "header": h, "rows": r}
    return out


def ap(e, which="all", stat="ap"):
    return float(e["rows"][f"{which}_{stat}"]) if e else math.nan


def trigger(fp32_ap, published):
    """§5: (FP32 AP@[.5:.95] in points rounded to 0.1, its difference from the published value, whether
    that difference is more than 2.0 points, None without an evaluation). The difference is taken on the
    rounded value, to 0.1."""
    rounded = round(100 * fp32_ap, 1)
    diff = round(rounded - published, 1)
    return rounded, diff, (None if math.isnan(diff) else abs(diff) > TRIGGER_POINTS)


def accuracy(root, commit):
    p, e = passes(root, commit), evaluations(root, commit)
    lines = ["## Accuracy (§2, §6)", "",
             "COCOeval (`iouType` bbox, default parameters) over the 5,000 images of COCO val2017, in percent. "
             "INT8 runs on the board's NPU, FP32 on the PC (ONNX Runtime), on the same letterboxed inputs. "
             "The rule is §2.3's: conf 0.001, at most 300 detections per image; YOLOv8n with class-wise NMS at 0.7, "
             "YOLOv10n without NMS.", "",
             "| Model | Precision | " + " | ".join(label for _, label in STATS) + " |",
             "|---|---|" + "---|" * len(STATS)]
    for model, name in MODELS:
        for precision in ("int8", "fp32"):
            ev = e.get((model, precision, "map"))
            lines.append(f"| {name} | {precision.upper()} | "
                         + " | ".join(fmt(100 * ap(ev, "all", k), 1) for k, _ in STATS) + " |")
    lines += ["", "**Quantization cost, INT8 − FP32** (percentage points):", "",
              "| Model | " + " | ".join(label for _, label in STATS) + " |", "|---|" + "---|" * len(STATS)]
    for model, name in MODELS:
        i8, f32 = e.get((model, "int8", "map")), e.get((model, "fp32", "map"))
        lines.append(f"| {name} | " + " | ".join(signed(100 * (ap(i8, "all", k) - ap(f32, "all", k)), 2)
                                                for k, _ in STATS) + " |")
    lines += ["", "**Without the 20 quantization images** (4,980 images, COCOeval's `params.imgIds`; the "
              "5,000-image values are the main ones):", "",
              "| Model | Evaluated | AP@[.5:.95], 5,000 | AP@[.5:.95], 4,980 | AP50, 5,000 | AP50, 4,980 |",
              "|---|---|---|---|---|---|"]
    for model, name in MODELS:
        for precision, subset, label in (("int8", "map", "INT8"), ("fp32", "map", "FP32"),
                                         ("int8", "pipeline", "INT8, the pipeline's settings")):
            ev = e.get((model, precision, subset))
            lines.append(f"| {name} | {label} | {fmt(100 * ap(ev), 2)} | {fmt(100 * ap(ev, 'without_quantization'), 2)} | "
                         f"{fmt(100 * ap(ev, 'all', 'ap50'), 2)} | {fmt(100 * ap(ev, 'without_quantization', 'ap50'), 2)} |")
    lines += ["", "**The pipeline's settings** (§2.4), INT8 on the board: the post-processing the pipeline runs, "
              "evaluated on COCO's still images. Not comparable with published values (§2.4).", "",
              "| Model | Post-processing | " + " | ".join(label for _, label in STATS) + " |",
              "|---|---|" + "---|" * len(STATS)]
    for model, name in MODELS:
        ev = e.get((model, "int8", "pipeline"))
        rule = ev["header"].get("rule", "") if ev else ""
        lines.append(f"| {name} | {rule} | " + " | ".join(fmt(100 * ap(ev, "all", k), 1) for k, _ in STATS) + " |")
    lines += ["", "**Context: the published values** (§5), obtained under other settings (Ultralytics' and "
              "THU-MIG's own validation, rectangular inputs, their own exports):", "",
              "| Model | Published AP@[.5:.95] | M4's FP32, rounded to 0.1 | Difference (points) | §5's trigger (more than 2.0) |",
              "|---|---|---|---|---|"]
    for model, name in MODELS:
        f32, diff, fired = trigger(ap(e.get((model, "fp32", "map"))), PUBLISHED[model])
        lines.append(f"| {name} | {PUBLISHED[model]:.1f} | {fmt(f32, 1)} | {signed(diff, 1)} | "
                     f"{'–' if fired is None else 'fired' if fired else 'not fired'} |")
    lines += ["", "**Detections kept** (§6):", "",
              "| Model | Evaluated | Record | Detections | Per image | Images at 300 | Of those, 300th score tied |",
              "|---|---|---|---|---|---|---|"]
    for model, name in MODELS:
        for precision, subset, label in (("int8", "map", "INT8"), ("fp32", "map", "FP32"),
                                         ("int8", "pipeline", "INT8, the pipeline's settings")):
            ev = e.get((model, precision, subset))
            if ev:
                r = ev["rows"]
                lines.append(f"| {name} | {label} | `{ev['record']}` | {int(r['detections']):,} | "
                             f"{r['detections_per_image_mean']} | {int(r['images_at_300']):,} | "
                             f"{int(r['images_tied_at_300']):,} |")
    lines += ["", "**The passes** (§2.5):", "",
              "| Model | Precision | Record | Outcome | Images | Seconds | Checks |", "|---|---|---|---|---|---|---|"]
    missing = []
    for model, name in MODELS:
        for precision in ("int8", "fp32"):
            attempts = p.get((model, precision), [])
            if not attempts:
                missing.append(f"{name} {precision.upper()}")
            for a in attempts:
                r = a["rows"]
                checks = []
                if precision == "int8":
                    checks.append(f"bound outputs checked against `rknn_outputs_get` on {r.get('reference_checks', '–')} images")
                    checks.append(f"{r.get('dump_images_differing', '–')} of {r.get('dump_images_compared', '–')} "
                                  "dump images differ from §8 step 3's")
                    if "pipeline_neon_vs_yolov8_post_images_differing" in r:
                        checks.append(f"NeonPost and `yolov8_post` differ on "
                                      f"{r['pipeline_neon_vs_yolov8_post_images_differing']} images")
                lines.append(f"| {name} | {precision.upper()} | `{a['record']}` | {a['outcome']} | "
                             f"{r.get('images', '–')} | {r.get('seconds', '–')} | {'; '.join(checks) or '–'} |")
    if missing:
        lines += ["", "Missing passes: " + ", ".join(missing) + "."]
    return lines + [""]


# ---------------------------------------------------------------- calib_npu

def calib_session(root):
    """The calib_npu session the pipeline sessions took: the one their saturation.txt names, the same in all."""
    named = set()
    for sweep in POINTS:
        for d in rep.sessions_of(root, sweep):
            named.add(om.keyed(d / "saturation.txt").get("calib_npu_session"))
    if len(named) != 1 or None in named:
        raise SystemExit(f"m4_report: the pipeline sessions name calib_npu sessions {sorted(map(str, named))}")
    return root / named.pop()


def capacity(root):
    d = calib_session(root)
    h, r = key_values(d / "m4_calibration.csv")
    lines = ["## NPU capacity (§3.1)", "",
             f"From [`{d.name}`](../../results/m4/{d.name}) ({h.get('ambient_c', '–')} °C), the session the pipeline "
             "sessions took. Medians of 3 runs on M1-3's 1,000 tensors; times are means over a run.", "",
             "| Model | Measurement | Inferences/s | Valid runs | `rknn_run` (ms) | Bind (ms) | Post-processing (ms) | NPU load, cores 0/1/2 (%) |",
             "|---|---|---|---|---|---|---|---|"]
    p_cap = {}
    for model, name in MODELS:
        for m, label in (("p_cap", f"`P_cap`, 3 cores, with `{h.get(f'post_{model}_p_cap', '?')}`"),
                         ("npu_raw_1c", "raw, 1 core")):
            k = f"{model}_{m}"
            loads = "/".join(r.get(f"{k}_npu_load_core{c}", "–") for c in range(3))
            lines.append(f"| {name} | {label} | {r.get(f'{k}_inf_per_s', '–')} | {r.get(f'{k}_valid_runs', '–')} | "
                         f"{r.get(f'{k}_run_ms', '–')} | {r.get(f'{k}_bind_ms', '–')} | {r.get(f'{k}_post_ms', '–')} | {loads} |")
        p_cap[model] = float(r[f"{model}_p_cap_inf_per_s"])
    m1h, m1 = key_values(M1_CALIB) if M1_CALIB.exists() else ({}, {})
    if m1:
        lines += ["", f"For reference, M1-3 ([`{M1_CALIB.parent.name}`](../../results/calibration/{M1_CALIB.parent.name}), "
                  f"code `{m1h.get('code_commit', '')[:7]}`): YOLOv8n `P_cap` {m1['p_cap_inf_per_s']}, with M1's "
                  f"`yolov8_post`, not Cp's; raw rate on 1 core {m1['npu_raw_1c_inf_per_s']}."]
    lines += ["", "**Saturation labels** (§3.2): a point is unsaturated for a model only if channels × fps ≤ "
              f"{SATURATION_SHARE} × `P_cap`.", "",
              "| Point | Load (inferences/s) | " + " | ".join(f"{a} ({ARM_MODEL[a]}): 0.8 × `P_cap`" for a in ARMS)
              + " | " + " | ".join(f"{a}'s label" for a in ARMS) + " |",
              "|---|---|" + "---|" * (2 * len(ARMS))]
    for sweep, (point, ch, fps) in POINTS.items():
        load = ch * fps
        bounds = [SATURATION_SHARE * p_cap[ARM_MODEL[a]] for a in ARMS]
        labels = [r.get(f"{ARM_MODEL[a]}_{point}_point", "–") for a in ARMS]
        lines.append(f"| {point}, {ch} × {fps} | {load} | " + " | ".join(f"{b:.1f}" for b in bounds) + " | "
                     + " | ".join(labels) + " |")
    lines += ["", "A saturated arm's runs are still made. Rule 3 of PROTOCOL §9.3 does not judge them, and the "
              "comparison at that point is between unequal loads (§3.2).", ""]
    return lines


# ---------------------------------------------------------------- pipeline

def group(comm):
    """A thread's group, as tools/cpu_breakdown.py and the M2 report group them."""
    if comm.startswith("npu-"):
        return "npu"
    if comm.startswith(("dec-", "mpp_")):
        return "dec"
    if comm.startswith("pre-"):
        return "pre"
    return "rest"


def run_metrics(run_dir, idle, sweep):
    m = m2r.run_m2_metrics(run_dir, idle, sweep)
    for g in ("npu", "dec", "pre", "rest"):
        m[f"group_{g}_s"] = 0.0
    for row in ss.read_csv(run_dir / "threads.csv")[1]:
        m[f"group_{group(row['comm'])}_s"] += float(row["cpu_s_in_window"])
    for name, (p50, p99) in stg.run_stages(run_dir).items():
        m[f"stage_{name}_p50_ms"], m[f"stage_{name}_p99_ms"] = p50, p99
    m.pop("_all_frames", None)
    return m


def collect(root):
    """{sweep: {"points": {arm: {repetition: metrics}}, "labels", "missing", "invalid_x2", "others",
    "warnings", "sessions"}}."""
    out = {}
    for sweep, (point, ch, _) in POINTS.items():
        sessions = rep.sessions_of(root, sweep)
        idle = {d.name: rep.read_idle(d) for d in sessions if (d / "idle_baseline.csv").exists()}
        runs, others = rep.counted_runs(sessions, sweep)
        arms = {a: {} for a in ARMS}
        missing, invalid = [], []
        for r in range(1, ss.REPETITIONS + 1):
            for arm in ARMS:
                run = runs.get((ch, r, arm))
                if run is None:
                    missing.append(f"{sweep} {arm} r{r}")
                elif run["status"] == "valid":
                    d = root / "results/m4" / run["session"] / run["name"]
                    arms[arm][r] = dict(run_metrics(d, idle[run["session"]], sweep), _run=f"{run['session']}/{run['name']}")
                else:
                    invalid.append((f"{sweep} {arm} r{r}", run["attempts"]))
        labels = {}
        for d in sessions:
            sat = om.keyed(d / "saturation.txt")
            labels.update({a: sat.get(f"label_{a}") for a in ARMS})
        out[sweep] = {"points": arms, "labels": labels, "missing": missing, "invalid_x2": invalid,
                      "others": others, "warnings": rep.trip_warnings(sessions),
                      "sessions": [(d.name, key_values(d / "result.csv")[0].get("ambient_c", "–")) for d in sessions]}
    return out


def mean_sd(runs, key, digits):
    n, mean, sd, _ = rep.stats([r[key] for r in runs.values()])
    return "–" if n == 0 else f"{fmt(mean, digits)} ± {fmt(sd, digits)} ({n})"


def differences(c8, c10, key, relative=False):
    """{repetition: C10_r − C8_r (or its share of C8_r)} over the repetitions with both arms valid."""
    out = {}
    for r in sorted(c8.keys() & c10.keys()):
        d = c10[r][key] - c8[r][key]
        out[r] = d / c8[r][key] if relative else d
    return out


def interval_text(diffs, form):
    """("r1 x, r2 y, …", "mean [low, high]") of paired differences, formatted with form; no interval below 2."""
    if not diffs:
        return "–", "–"
    mean, lo, hi = m2r.t_interval(list(diffs.values()))
    pairs = ", ".join(f"r{r} {form(x)}" for r, x in diffs.items())
    return pairs, (f"{form(mean)} [{form(lo)}, {form(hi)}]" if len(diffs) >= 2 else f"{form(mean)} (no interval)")


def pipeline(c):
    lines = ["## Pipeline (§3.2, §3.3)", "",
             "C8 is YOLOv8n with Cp's post-processing (`--post neon`), C10 YOLOv10n with `yolov10_post` (`--post v10`), "
             "both variant C from the same binary. Values are the mean ± sample sd (valid runs) per 120 s window. "
             "Differences are C10_r − C8_r per repetition, with their mean and a descriptive 95% t interval "
             "(2 degrees of freedom); for CPU also (C10_r − C8_r) / C8_r. No threshold and no verdict (§3.2).", ""]
    for sweep, (point, ch, fps) in POINTS.items():
        s = c[sweep]
        p = s["points"]
        labels = ", ".join(f"{a} {s['labels'].get(a, '–')}" for a in ARMS)
        sessions = ", ".join(f"`{n}` ({amb} °C)" for n, amb in s["sessions"])
        lines += [f"### {sweep}: {ch} channels × {fps} fps, {point} point", "",
                  f"Session {sessions}. Saturation labels: {labels}.", "",
                  "| Metric | C8 | C10 | C10_r − C8_r | Mean [95% t interval] |", "|---|---|---|---|---|"]
        for key, label, digits, relative in METRICS + STAGE_METRICS:
            if key in MAIN_ONLY and point != "main":
                continue
            diffs = differences(p["C8"], p["C10"], key)
            pairs, mean = interval_text(diffs, lambda x, d=digits: signed(x, d))
            lines.append(f"| {label} | {mean_sd(p['C8'], key, digits)} | {mean_sd(p['C10'], key, digits)} | {pairs} | {mean} |")
            if relative:
                rel = differences(p["C8"], p["C10"], key, relative=True)
                pairs, mean = interval_text(rel, pct)
                lines.append(f"| {label}, (C10 − C8) / C8 | | | {pairs} | {mean} |")
        lines += ["", "CPU by thread group (exploratory; `threads.csv`, the threads alive at the window's end):", "",
                  "| Thread group | C8 | C10 | C10_r − C8_r | Mean [95% t interval] |", "|---|---|---|---|---|"]
        for key, label, digits, _ in GROUP_METRICS:
            pairs, mean = interval_text(differences(p["C8"], p["C10"], key), lambda x, d=digits: signed(x, d))
            lines.append(f"| {label} | {mean_sd(p['C8'], key, digits)} | {mean_sd(p['C10'], key, digits)} | {pairs} | {mean} |")
        cv = {a: rep.stats([r["process_cpu_s"] for r in p[a].values()])[3] for a in ARMS}
        flags = [a for a in ARMS if cv[a] > rep.CV_FLAG]
        lines += ["", "CV of process CPU: " + ", ".join(f"{a} {pct(cv[a]).lstrip('+')}" for a in ARMS)
                  + (f"; above 10% (PROTOCOL §9.3): {', '.join(flags)}." if flags else "; none above 10% (PROTOCOL §9.3)."), "",
                  "| Repetition | Arm | Run | Process CPU (s) | schedstat − idle (s) | `rknn_run` p50 (ms) | Post p50 (ms) | p99 (ms) |",
                  "|---|---|---|---|---|---|---|---|"]
        for r in range(1, ss.REPETITIONS + 1):
            for arm in ARMS:
                m = p[arm].get(r)
                if m:
                    lines.append(f"| {r} | {arm} | `{m['_run']}` | {fmt(m['process_cpu_s'])} | "
                                 f"{fmt(m['schedstat_total_minus_idle_s'])} | {fmt(m['stage_run_p50_ms'])} | "
                                 f"{fmt(m['stage_post_p50_ms'])} | {fmt(m['latency_p99_ms'], 2)} |")
        notes = ([f"- missing: {x}" for x in s["missing"]]
                 + [f"- invalid ×2: {k}: " + "; ".join(f"{a} ({why})" for a, why in at) for k, at in s["invalid_x2"]]
                 + [f"- attempt that does not count: {a}, {'valid' if ok else 'invalid'} ({why})" for a, ok, why in s["others"]]
                 + [f"- samples at a trip point: {a}: {n}" for a, n in s["warnings"]])
        lines += [""] + (notes or ["Every repetition has both runs valid at the first attempt; no sample at a trip point."]) + [""]
    return lines


def against_m2(root, c):
    """M4's C8 next to M2's Cp at the same points (§6, exploratory)."""
    try:
        m2 = m2r.collect(root)
    except (OSError, KeyError) as e:
        return ["## M4's C8 next to M2's Cp (exploratory)", "", f"M2's records could not be read: {e}", ""]
    keys = (("process_cpu_s", "Process CPU (s)", 3), ("schedstat_total_minus_idle_s", "System CPU, schedstat, minus idle (s)", 3),
            ("post_p50_ms", "Post-processing stage p50 (ms)", 3), ("latency_p50_ms", "Latency p50 (ms)", 2),
            ("latency_p99_ms", "Latency p99 (ms)", 2))
    lines = ["## M4's C8 next to M2's Cp (exploratory, §6)", "",
             "The same arm (C with `--post neon`, YOLOv8n) from another binary and session. Mean ± sample sd (valid runs).", "",
             "| Point | Metric | M2's Cp | M4's C8 |", "|---|---|---|---|"]
    for sweep, m2_sweep in M2_SWEEPS.items():
        for key, label, digits in keys:
            lines.append(f"| {POINTS[sweep][0]} | {label} | {mean_sd(m2[m2_sweep]['points']['Cp'], key, digits)} | "
                         f"{mean_sd(c[sweep]['points']['C8'], key, digits)} |")
    return lines + [""]


def report(root):
    commit = freeze_m4(root)
    c = collect(root)
    lines = ["# M4: tables", "",
             f"From the records of kind `m4` under `results/m4/`, made after `freeze-m4` (`{commit[:7]}`) "
             "([prereg/M4.md](../../prereg/M4.md), AMENDMENTS A14). M4 has no hypotheses: nothing here is a verdict. "
             "Method: `tools/m4_report.py`.", ""]
    lines += accuracy(root, commit) + capacity(root) + pipeline(c) + against_m2(root, c)
    return "\n".join(lines).rstrip("\n") + "\n"


def main(argv):
    ap_ = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap_.add_argument("--out")
    args = ap_.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    text = report(REPO)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8", newline="\n")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
