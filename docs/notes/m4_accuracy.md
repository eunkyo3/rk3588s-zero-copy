[한국어](m4_accuracy.ko.md)

# M4's accuracy: tools and procedure

How M4's accuracy (prereg/M4.md §2) is run.
- **Normative:** [prereg/M4.md](../../prereg/M4.md). This note says how the tools apply it.
- **Covered so far:** the evaluation image, COCO's files, the inputs, the Python rules, the PC's FP32 pass and the evaluation.
- **Not yet:** the board's INT8 pass, the C++ YOLOv8n rule and the check of §8 step 3 between the two implementations. They are added here with their tools.

## The evaluation image (§8 step 2)

- **[tools/eval/Dockerfile](../../tools/eval/Dockerfile):** the converter image, unchanged, plus `pycocotools` 2.0.11.
  - **The converter image:** built from [tools/model/Dockerfile](../../tools/model/Dockerfile) on `python:3.10-slim-bookworm@sha256:5be5aaec…`. Its image ID is `sha256:3ea3796529342281fdb543b8911261112e5ab942798c2fea4363edf3eca67f8f`, and it converted both models.
  - **pycocotools:** pinned by its wheel's SHA-256 ([requirements.txt](../../tools/eval/requirements.txt)). It needs only numpy, which the base has; `pip check` finds nothing broken.
- **Packages:** Python 3.10, numpy 1.26.4, opencv-python 4.11.0.86, onnxruntime 1.23.2, pycocotools 2.0.11.
- **The image:** `sha256:c676e76f95356ea1aac39a87694ac5a498207890922f2a01c9fdbedd773f250a`, created 2026-11-16T05:31:15Z.
  - **Fixed in the repository:** the ID is in [tools/eval/image_id.txt](../../tools/eval/image_id.txt). `build.sh` and `pc_run.py` refuse any other image, and the runs use the image by this ID.
  - **The ID itself:** Docker Desktop's containerd image store, which must stay on, makes it the image index's digest (its `RepoDigests`). The index lists the linux/amd64 manifest `sha256:aa27b59bbb633a0c5e209326e5c970bfa033710d32491fd41cba555908e69a06`, whose config is `sha256:01691948e3ba4917bae6d056b9e18f3d0ef886e34cea08f608e799b36f494f29` (read from the archive below). Without the containerd store, Docker would call the image by that config digest.
  - **An earlier build:** one build that day printed `sha256:e23dd324…`. It came before `build.sh` stopped rebuilding.
- **Build and checks:** `MSYS_NO_PATHCONV=1 tools/eval/build.sh`.
  - **Once:** it never builds over the image. A missing image is restored with `docker load -i` from the archive below, which keeps its ID. A rebuild may give a new ID; `--rebuild` forces one, and its ID must then go to `image_id.txt` by a commit.
  - **What it checks:**
    - the base image's ID, and that the image's layers start with the base's;
    - the image's ID against `image_id.txt`;
    - that `pip freeze` equals [constraints.txt](../../tools/eval/constraints.txt), the converter's plus pycocotools;
    - `pip check`, and that pycocotools' COCOeval imports.
  - **Record:** `results/check/<UTC>_m4_eval_image/image.txt`.
    - **06:59:49Z on 2026-11-16:** passed every check.
    - **06:44:30Z on 2026-11-16:** an earlier record. Its `pip check` and import could not fail: a pipe took their exit status. A review found this, and the script was fixed.
- **Archive:** `MSYS_NO_PATHCONV=1 tools/eval/save_image.sh` saves the image with `docker save`, base layers included, outside the repository.
  - **Why:** a reset of Docker Desktop or a prune would otherwise lose it.
  - **Restore:** `docker load -i <file>`.
  - **The archive of 2026-11-16:** `D:/m4data/images/m4-eval_c676e76f95356ea1.tar`, 583,230,976 bytes, SHA-256 `ae9a7dcfd59672e8573cda16c7985c99542c0e8f0c4c2543ca9f38d3f53d71ab`. Its record is `results/check/<UTC>_m4_eval_image_archive/archive.txt`.

## COCO's files (§2.1)

- **Download:** `tools/eval/fetch_coco.sh [DATA]` (default `D:/m4data`) puts `val2017.zip` and `annotations_trainval2017.zip` in `DATA/coco`, outside the repository.
  - It then checks them against [tools/eval/coco.sha256](../../tools/eval/coco.sha256).
  - A file already there is checked, not downloaded again; a bad one has to be deleted first.
- **Source:** the S3 bucket `images.cocodataset.org`, over HTTPS only (redirects included) at `s3.amazonaws.com/images.cocodataset.org`.
  - The bucket's own name has no matching certificate: `https://images.cocodataset.org` fails with a name mismatch.
  - Plain `http://` gives no integrity in transit.
- **The download of 2026-11-16:**
  - **Sizes:** 815,585,330 and 252,907,541 bytes.
  - **S3 `Last-Modified`:** 2018-07-11 and 2018-07-10.
  - **MD5:** `442b8da7639aecaf257c1dceb8ba8c80` and `f4bbac642086de4f52a3fdda2de5fa2c`, the values commonly published for these files. The annotations' MD5 is also its S3 `ETag`.
- **Licences:** the images keep their Flickr licences and stay outside the repository. The annotations are under CC BY 4.0.

## Runs on the PC (§4)

[tools/eval/pc_run.py](../../tools/eval/pc_run.py) runs a tool of the repository in the evaluation image:
`MSYS_NO_PATHCONV=1 python tools/eval/pc_run.py --kind <check|m4> --name <name> [--data D:/m4data] -- <tool> <args>`.
- **From Git Bash:** `MSYS_NO_PATHCONV=1` is required, and the tool checks it. Without it, Git Bash turns the container's `/data/...` into a Windows path. `--data` takes a Windows path (`D:/...`).
- **Before the tool starts, it checks:**
  - **the code:** `HEAD`. The tool must be tracked by git. A tracked file that differs from `HEAD`, or an untracked file under `tools/` or `tests/` that the tool could import, refuses the run. `--allow-dirty` lets a development check run anyway, and is recorded; a run of kind `m4` never takes it;
  - **the image:** the image of `image_id.txt`, by that ID;
  - **the CPU model;**
  - **the host clock:** its offset from NTP, from `w32tm /stripchart /computer:time.windows.com /samples:3 /dataonly`. The samples that answer count, and the others are recorded;
  - **the clock's sync state:** `w32tm /query /status`;
  - **the container clock:** its offset from the host's, from the shortest of 5 exchanges over one container.
- **Refusal:** the tool does not start with no NTP answer, or with either offset above 1 s. A run of kind `m4` does not start before `freeze-m4` has its row in env.md.
- **The run:**
  - **Record:** `results/<kind>/<UTC>_<name>/`. Its `pc.json` holds the above, the start time from the host clock, the run kind, M4's stage commits and the amendment in force (PROTOCOL §1.4, M4 §4). So even a tool that stops early leaves a header that `tools/check_prereg_order.py` checks.
  - **Container:** run by the image's ID. The repository is mounted read-only at `/repo`, the record at `/record`, and the data at `/data`. It has no network and no new privileges.
  - **Limits:** 2 GB of memory (4 GB with swap), as for the conversion. Other projects' containers share the Docker VM.
  - **The evaluation's limit:** `tools/m4_coco.py` gets 6 GB without swap (see Evaluation below). Without swap, a run that needs more fails at once instead of thrashing the disk. It does not start while another container runs, or when the Docker VM has less than 7 GB. `pc.json` records the limit as `container_memory`.
  - **Python:** `python3 -I`, since the inputs are downloaded data.
  - **`--checks-only`:** records `pc.json` and runs no tool, to record the PC's clock, say.
- **The PC:** Intel Core i7-6500U.
- **Its clock on 2026-11-16:**
  - **Until about 06:14 UTC:** the Windows Time service was not running. `w32tm /query /status` failed with `0x80070426`, which the inputs run's `pc.json` records. The stripchart samples that answered gave +0.75 to +0.76 s, within §4's 1 s, but nothing corrected the clock.
  - **At about 06:14 UTC, as the user reported:** the user set the service to start automatically, pointed it at `time.windows.com`, and resynchronized. Right after, the stripchart still gave +0.78 s.
  - **At 06:48 UTC:** `results/check/20261116T064832Z_pc_clock` recorded +0.59 s (1 of 3 samples answered). `w32tm /query /status` succeeded, synchronized from `time.windows.com` at 15:14:56 local time.
  - **Why it persists:** below 1 s, a standalone Windows PC's defaults correct the clock gradually rather than in one step. It went from +0.78 s to +0.59 s in about 35 minutes. It is measured again before the PC's passes.

## The inputs and the data checks (§2.2, §8 step 3)

`MSYS_NO_PATHCONV=1 python tools/eval/pc_run.py --kind check --name m4_inputs -- tools/m4_prep.py --coco /data/coco --out /data/<new directory>`

- **What [tools/m4_prep.py](../../tools/m4_prep.py) does:**
  - **Zips first:** checks the two files' SHA-256. If either differs, it reads nothing else.
  - **One to one:** checks that val2017's 5,000 JPEGs and the annotations' 5,000 images match one to one, each id once and named after it. Only `val2017/<12 digits>.jpg` members are written, to `DATA/coco/val2017/`.
  - **Preparation:** prepares each image as §2.2 says, and reads each PNG back.
  - **Quantization images:** checks the 20 against [models/coco_subset_20.sha256](../../models/coco_subset_20.sha256).
- **Outputs:**
  - **Inputs:** the 5,000 PNGs, in the new directory, as `<image id>.png`.
  - **Records:** `manifest.tsv` and `m4_inputs.csv` in the record directory.
  - **Headers:** both carry `pc.json`'s header, and OpenCV's version and optimization state.
  - **Hashes:** `m4_inputs.csv` gives the SHA-256 of the whole manifest, and of its rows alone. The rows alone (the column line and the data, without the header) are the same for any run with the same pixels.
- **The board's check:** that it decodes all 5,000 PNGs to the manifest's bytes comes with the board tool.
- **The tests:** those that need OpenCV run in the image, with a record: `MSYS_NO_PATHCONV=1 python tools/eval/pc_run.py --kind check --name m4_prep_tests -- tests/test_m4_prep.py`.
- **Runs:**
  - **`results/check/20261116T053859Z_m4_inputs`:** stopped at once. It was run from Git Bash without `MSYS_NO_PATHCONV=1`, which turned `/data/coco` into a Windows path. `pc_run.py` now refuses that.
  - **`results/check/20261116T054025Z_m4_inputs`:** 38 minutes, `passed,1`, PNGs in `D:/m4data/inputs`.
    - **Images:** all 5,000, each decoded at its annotated size. 903 were scaled; 4,097 already had 640 as their longer side.
    - **PNGs:** every one read back to the canvas's pixels.
    - **Quantization images:** all 20 are among the 5,000, each with its recorded SHA-256.
    - **`manifest.tsv`:** SHA-256 `f3ad1dbee36fd6109637374a75c16ff7a6bb150d0e266b2df02d6f8215e1d766`.
    - **Superseded:** a review then asked for its header to carry `freeze_m1_commit` (PROTOCOL §1.4) and for the checks above. So it was rerun with the final tool, below.
  - **`results/check/20261116T064917Z_m4_inputs`, the inputs M4 uses:** the rerun with the final tool (commit `9dbf814`), 37 minutes, `passed,1`, PNGs in `D:/m4data/inputs2`.
    - **Same counts:** 5,000 images, 903 scaled, no size or PNG mismatch, the 20 quantization images with their SHA-256.
    - **`manifest_rows_sha256`:** `5a63ad71886c7d670184bd1f91601a719d6d0f456d5e5c35bfa93c2418d69a69`, equal to the first run's rows. So the 5,000 inputs came out byte for byte the same.
    - **`manifest.tsv`:** SHA-256 `b29adf50175677fd031adbc2600f68a5115a075c80095b73d5b8665b35ffc444`, with its header.
    - **Before it:** NTP offset +0.582 s (1 of 3 samples answered), the container −0.003 s. The Windows Time service was running.
    - **OpenCV:** 4.11.0, optimized.

## The rules in Python (§2.3)

[tools/m4_rules.py](../../tools/m4_rules.py) holds both rules. They run on float32 outputs. INT8 outputs are made float32 by `dequantize`, the C++ expression `(q − zp) × scale`.
- **Arithmetic:** float32, in the C++'s order (`src/core/yolov10_post.cpp`, `yolov8_post.cpp`).
  - A candidate is a (cell, class) pair whose score is above 0.001.
  - Candidates are ordered by score; equal scores by branch, then cell, then class.
  - Each box is decoded from its own cell: per side, the softmax's expectation over the 16 DFL bins, summed bin by bin; then `(x + 0.5 − d) × stride`.
  - **Bit for bit with the board:** `exp` is computed in float64 and rounded once to float32, as the board's `expf` gives it; numpy's float32 `exp` is off by up to 2 ulp. `acc += e × i` is rounded once, as the fused multiply-add GCC makes of it on the board. Without these, boxes differed by up to 6e-5 px, and the IoU of two boxes by up to 3.5e-6, more than the 1e-6 that §2.3 allows around 0.7.
  - **The branches' order:** they must come in stride 8, 16, 32 order, since the tie order follows it.
- **`yolov10_rule`:** the 300 best candidates, no NMS.
- **`yolov8_rule`:** the first 30,000 candidates, then a greedy NMS per class going down their order. A candidate is dropped when its IoU (computed as `yolov8_post` does) with an earlier kept candidate of its class is above 0.7. The first 300 kept are the result. The score sums are not used.
- **Ties (§6):** each rule also says whether its 301st detection has the 300th's score.
- **Against the board:** on the exploration's board dumps (21 images, YOLOv10n INT8), `yolov10_rule` gives `yolov10_post`'s lists at conf 0.25 and 0.001: all 42 with the same count, order and classes.
  - **Bit for bit:** all 32,530 scores and box coordinates are the board's, on the host and in the image. Before the decode above, 2,693 differed, by up to 6.2e-5 px; §2.3 allows 1e-6 in score and 1e-3 px in box.
  - **Test:** a test repeats this wherever the dumps are (they are not committed). It checks §2.3's tolerances, then every bit.
  - **Still to come:** the check of §8 step 3, on the first 50 images and for both rules, comes with the board tool.
  - **For the board's YOLOv8n rule:** its candidates should use `yolov10_post`'s `first_above`, as the Python does. `yolov8_post`'s `s > quantize(conf)` is not always the same test. GCC may also contract the IoU's union into a fused multiply-add, which moves the IoU by about 1e-7, within §2.3's 1e-6.

## Detections files

A pass writes two tables to a directory of its own, outside the repository: `D:/m4data/passes/<record>/`. Each has a `# key: value` header (`pc.json`'s fields, the pass, the model and its SHA-256, the precision, the rule, the manifest's SHA-256s).
- **`detections.tsv`:** `image_id, rank, class, score, x1, y1, x2, y2`.
  - **Order:** one row per detection, the images in the manifest's order, each image's detections in the rule's order.
  - **Values:** the score and the box (model input coordinates) are float32 printed with 9 significant digits, as the board's C++ prints them.
- **`images.tsv`:** `image_id, candidates, detections, tied`.
- **Both precisions:** the board's INT8 pass will write the same two files.

## The FP32 pass on the PC (§2.2, §2.5)

`MSYS_NO_PATHCONV=1 python tools/eval/pc_run.py --kind m4 --name fp32_<model> -- tools/m4_fp32.py --model <yolov8n|yolov10n> --manifest results/check/20261116T064917Z_m4_inputs/manifest.tsv --inputs /data/inputs2`

- **The model:** its SHA-256 must be §1's (`models/<model>.sha256`) before anything runs.
- **onnxruntime:** the CPU execution provider, 1 intra-op and 1 inter-op thread, the default graph optimization.
- **Each image:**
  - in the manifest's order, the PNG is decoded and made RGB;
  - its bytes must be the manifest's; otherwise the pass is invalid (§5) and stops;
  - the model sees RGB / 255 in float32, NCHW;
  - the model's rule gives the detections.
- **Records:** the detections files go to `D:/m4data/passes/<record>/`. `pass.csv` in the record directory has the counts, both files' SHA-256, and `valid`.
  - **Any error once the pass has started** makes it invalid: `valid,0`, with the error as the reason, and exit 1.
  - **The files' header** also names the pass's record, where `tools/m4_coco.py` finds `pass.csv`.
- **Scope:** a pass of kind `m4` runs all 5,000 images. `--limit N` (N ≥ 1) runs the first N, for a check only.
- **Speed:** on this PC, per image (5 images, onnxruntime plus the rule):
  - YOLOv8n: 0.87 s + 0.21 s, so a pass takes about 1.5 hours;
  - YOLOv10n: 0.70 s + 0.05 s, about 1 hour.
  - Other work on the PC slows it: with another project's build and a Windows update running, the 20-image checks below took 4.5 s and 2.1 s per image.
- **Checks of 2026-11-17 (`--limit 20`, kind `check`):** `results/check/20261117T021343Z_fp32_yolov8n_limit20` and `…T021553Z_fp32_yolov10n_limit20`, both `valid,1`, at commit `3251204`, before the review's changes to the decode. They checked the files and the speed. Their detections were not evaluated: AP is computed after freeze-m4 only.
- **Tests in the image:** `results/check/20261117T034320Z_m4_rules_tests` (11, the bit-for-bit board comparison included), `…T034351Z_m4_coco_tests` (11) and `…T034409Z_m4_fp32_tests` (6), at commit `d567e2c`. None was skipped. The runs at `3251204` (`…T021046Z`, `…T021205Z`, `…T021233Z`) came before the review.

## Evaluation (§2.3, §6)

`MSYS_NO_PATHCONV=1 python tools/eval/pc_run.py --kind <check|m4> --name eval_<pass> -- tools/m4_coco.py --dets /data/passes/<pass record> --manifest <manifest.tsv> --annotations /data/coco/annotations_trainval2017.zip`

- **Conversion ([tools/m4_coco.py](../../tools/m4_coco.py)), one function for both precisions:**
  - each box goes back to the image: minus `left` or `top`, divided by `r`, clipped to the image, as `[x, y, width, height]`;
  - the values are float32 printed with 9 significant digits;
  - class k becomes the k-th smallest category id;
  - each image's detections keep their order.
- **COCOeval:** `iouType` bbox, default parameters, over the pass's images, and again without the 20 quantization images.
- **`coco_eval.csv`:** COCOeval's 12 stats for both image sets; the detections per image; how many images reached 300 and how many of those were tied; the SHA-256 of the files.
- **The annotations:** checked against `coco.sha256`, and read from their zip.
- **What it evaluates:**
  - The pass's `manifest_rows_sha256` must be `--manifest`'s.
  - **A model's detections** (a header that names `model_sha256`) only in a run of kind `m4`, after `freeze-m4` (§8 step 5), so that no AP is seen before. The pass must also be of kind `m4`, its own record must say `valid,1`, and it must cover the manifest's 5,000 images, 20 of them the quantization images.
  - **Other detections** (synthetic ones, to check the tool) only in a run of kind `check`.
  - A class out of range, a value that is not finite, or no detections at all: exit 2, before COCOeval.
  - `coco_eval.csv` gives the pass's `valid` as `pass_valid` (`no record` without one).
- **Size:** a pass has about 1.5 million detections. Most of the memory goes to two places:
  - COCOeval's records per image, category and area range (`evaluate`);
  - the loaded results (`loadRes`).
- **Saving memory:** the tool frees the detections table before COCOeval, and loads the results once for both image sets.
- **Measured:** synthetic detections (random boxes and classes, 300 per image; no model's), in the image:
  - 500 images: peak 0.64 GB;
  - 2,500 images: peak 2.5 GB, exit 0 at 6 GB without swap;
  - growth is linear, so 5,000 images need about 4.9 GB, under the 6 GB limit.
- **A 2 GB limit is too small:** with 2 GB plus 2 GB of swap, 5,000 images were killed for memory after 34 minutes of swapping.

## Files

| File | Content |
|---|---|
| `tools/m4_rules.py` | the two rules in Python ([tests](../../tests/test_m4_rules.py)) |
| `tools/m4_fp32.py` | the PC's FP32 pass ([tests](../../tests/test_m4_fp32.py)) |
| `tools/m4_coco.py` | the detections files, their COCO conversion and their evaluation ([tests](../../tests/test_m4_coco.py)) |
| `tools/eval/Dockerfile`, `requirements.txt`, `constraints.txt`, `image_id.txt`, `build.sh`, `save_image.sh` | the evaluation image, its ID, its checks and its archive |
| `tools/eval/fetch_coco.sh`, `coco.sha256` | COCO's two files and their SHA-256 |
| `tools/eval/pc_run.py` | a run on the PC after §4's checks ([tests](../../tests/test_pc_run.py)) |
| `tools/m4_prep.py` | the inputs and the data checks ([tests](../../tests/test_m4_prep.py); those with OpenCV run in the image) |
| `results/check/<UTC>_m4_eval_image/`, `…_m4_eval_image_archive/` | the image's checks, and its archive's SHA-256 |
| `results/check/<UTC>_m4_inputs/` | `pc.json`, `manifest.tsv`, `m4_inputs.csv`, `m4_prep.log` |
| `results/check/<UTC>_m4_{rules,coco,fp32}_tests/` | `pc.json` and the tests' log, in the image |
| `results/check/<UTC>_fp32_<model>_limit20/` | `pc.json`, `pass.csv`, `m4_fp32.log` of a 20-image check pass |
