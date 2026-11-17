#!/usr/bin/env python3
"""A check of tools/m4_coco.py's time and peak memory on a pass's size, with synthetic detections: 300 per
image with random boxes, classes and scores (seed 0), for the manifest's first N images. No model's
detections are used, so nothing about the models' accuracy is seen. It is a check of the tool, not a
measurement of prereg/M4.md: its peak memory depends on the evaluation image and the data, not on the PC,
so it may run on any amd64 PC with Docker (docs/notes/m4_accuracy.md, "Evaluation").

In the evaluation image, with the repository at /repo, the annotations zip at /data/coco and a host
directory at /w (not a tmpfs, which would count in the container's memory), under pc_run's limit for
tools/m4_coco.py:

  docker run --rm --memory=6g --memory-swap=6g --network none -v <repo>:/repo:ro \\
      -v <dir with annotations_trainval2017.zip>:/data/coco:ro -v <empty dir>:/w -w /repo \\
      sha256:c676e76f95356ea1aac39a87694ac5a498207890922f2a01c9fdbedd773f250a \\
      python3 -I tools/eval/m4_coco_memory.py --images 5000

tools/m4_coco.py runs as pc_run runs it (python3 -I, a process of its own), in a run of kind check. Prints
and writes to /w/m4_coco_memory.txt: the PC, the container's limits, the time, m4_coco's exit code and
its peak resident memory. Exit: m4_coco's code (non-zero if it was killed).
"""
import argparse
import json
import platform
import resource
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(REPO / "tools"))
import m4_coco as mc  # noqa: E402
import m4_prep as mp  # noqa: E402
import m4_rules as mr  # noqa: E402

MANIFEST = REPO / "results/check/20261116T064917Z_m4_inputs/manifest.tsv"  # the inputs M4 uses
PER_IMAGE = 300


def synthetic_rows(ids, rng):
    """detections.tsv's rows: PER_IMAGE per image, scores descending, boxes inside the 640 × 640 input."""
    for image_id in ids:
        xy = rng.uniform(0, 600, (PER_IMAGE, 2)).astype(np.float32)
        wh = rng.uniform(5, 200, (PER_IMAGE, 2)).astype(np.float32)
        score = np.sort(rng.uniform(0.001, 1, PER_IMAGE).astype(np.float32))[::-1]
        cls = rng.integers(0, 80, PER_IMAGE)
        x2, y2 = np.minimum(xy + wh, np.float32(640)).T
        for k in range(PER_IMAGE):
            yield [image_id, k, int(cls[k]), mr.g9(score[k]), mr.g9(xy[k, 0]), mr.g9(xy[k, 1]), mr.g9(x2[k]),
                   mr.g9(y2[k])]


def read_first(path, *names):
    """The first readable file's text, stripped, or "n/a"."""
    for name in names:
        try:
            return Path(path, name).read_text().strip()
        except OSError:
            pass
    return "n/a"


def machine():
    cpu = next((l.split(":", 1)[1].strip() for l in read_first("/proc", "cpuinfo").splitlines()
                if l.startswith("model name")), platform.machine())
    total = next((l.split()[1] for l in read_first("/proc", "meminfo").splitlines() if l.startswith("MemTotal")), "0")
    from importlib.metadata import version
    return [("cpu", cpu), ("vm_memory_gb", f"{int(total) / 2**20:.1f}"),
            ("container_memory_max", read_first("/sys/fs/cgroup", "memory.max")),
            ("container_swap_max", read_first("/sys/fs/cgroup", "memory.swap.max")),
            ("python", platform.python_version()), ("numpy", np.__version__), ("pycocotools", version("pycocotools"))]


def watch(p, peak):
    """m4_coco's resident memory every 10 s; a line every minute."""
    last = 0.0
    while p.poll() is None:
        try:
            rss = int(next(l for l in Path(f"/proc/{p.pid}/status").read_text().splitlines()
                           if l.startswith("VmRSS")).split()[1]) // 1024
        except (OSError, StopIteration):
            rss = 0
        peak[0] = max(peak[0], rss)
        if time.time() - last >= 60:
            print(f"  {(time.time() - peak[1]) / 60:5.1f} min  m4_coco resident {rss} MB", flush=True)
            last = time.time()
        time.sleep(10)


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--images", type=int, default=5000, help="the manifest's first N images (default 5000)")
    ap.add_argument("--annotations", default="/data/coco/annotations_trainval2017.zip")
    ap.add_argument("--work", default="/w", help="an empty host directory")
    a = ap.parse_args(argv)
    work = Path(a.work)
    work.mkdir(parents=True, exist_ok=True)
    if any(work.iterdir()):
        print(f"m4_coco_memory: {work} is not empty", file=sys.stderr)
        return 2
    text = MANIFEST.read_text(encoding="utf-8")
    ids = list(mc.read_manifest(MANIFEST))[:a.images]
    (work / "pass").mkdir()
    (work / "record").mkdir()
    header = {"pass": "synthetic", "detections": f"{PER_IMAGE} per image, random (seed 0); no model's",
              "manifest_rows_sha256": mp.rows_sha256(text)}
    started = time.time()
    mc.write_table(work / "pass/detections.tsv", header, mc.DET_COLUMNS,
                   synthetic_rows(ids, np.random.default_rng(0)))
    mc.write_table(work / "pass/images.tsv", header, mc.IMAGE_COLUMNS,
                   [[i, PER_IMAGE, PER_IMAGE, 0] for i in ids])
    pc = {k: "synthetic memory check" for k in mp.PC_FIELDS}
    (work / "record/pc.json").write_text(json.dumps({**pc, "run_kind": "check", "record": "m4_coco_memory"}))
    print(f"m4_coco_memory: {len(ids)} images, {len(ids) * PER_IMAGE} detections written in "
          f"{time.time() - started:.0f} s; running tools/m4_coco.py", flush=True)
    peak = [0, time.time()]
    with open(work / "record/m4_coco.log", "w") as log:
        p = subprocess.Popen([sys.executable, "-I", str(REPO / "tools/m4_coco.py"), "--dets", str(work / "pass"),
                              "--manifest", str(MANIFEST), "--annotations", a.annotations,
                              "--record", str(work / "record")], stdout=log, stderr=subprocess.STDOUT)
        threading.Thread(target=watch, args=(p, peak), daemon=True).start()
        code = p.wait()
    rows = [*machine(), ("images", len(ids)), ("detections", len(ids) * PER_IMAGE),
            ("m4_coco_exit", code if code >= 0 else f"killed by signal {-code} (out of memory?)"),
            ("m4_coco_minutes", f"{(time.time() - peak[1]) / 60:.1f}"),
            ("m4_coco_peak_resident_mb", resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss // 1024),
            ("m4_coco_peak_resident_sampled_mb", peak[0]),
            ("container_memory_peak_mb", int(read_first("/sys/fs/cgroup", "memory.peak").replace("n/a", "0")) // 2**20)]
    if code != 0:
        rows.append(("m4_coco_log_tail", " | ".join(
            (work / "record/m4_coco.log").read_text(errors="replace").splitlines()[-3:])))
    summary = "".join(f"{k}: {v}\n" for k, v in rows)
    (work / "m4_coco_memory.txt").write_text(summary)
    print(summary, end="")
    return code if code >= 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
