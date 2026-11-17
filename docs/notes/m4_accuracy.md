[한국어](m4_accuracy.ko.md)

# M4's accuracy: tools and procedure

How M4's accuracy (prereg/M4.md §2) is run.
- **Normative:** [prereg/M4.md](../../prereg/M4.md). This note says how the tools apply it.
- **Covered:**
  - the evaluation image, COCO's files and the inputs;
  - the rules in Python and in C++, and the board tool;
  - the PC's FP32 pass;
  - the order of the runs and its gates;
  - the evaluation;
  - §8 step 3's checks at the code to be frozen.

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
  - **Close to the board, bit for bit:** `exp` is computed in float64 and rounded once to float32; numpy's float32 `exp` is off by up to 2 ulp. `acc += e × i` is rounded once, as the fused multiply-add GCC makes of it on the board. Without these, boxes differed by up to 6e-5 px, and the IoU of two boxes by up to 3.5e-6, more than the 1e-6 that §2.3 allows around 0.7.
  - **glibc's `expf`** gives the correctly rounded value for all but rare arguments. In the first 50 images' YOLOv8n outputs, 2 of about 1,750 distinct arguments came out 1 ulp apart, the same in the board's glibc and the image's. The Python rule keeps the correctly rounded value: calling `expf` for each element would add about 40 minutes to an FP32 pass.
  - **The branches' order:** they must come in stride 8, 16, 32 order, since the tie order follows it.
- **`yolov10_rule`:** the 300 best candidates, no NMS.
- **`yolov8_rule`:** the first 30,000 candidates, then a greedy NMS per class going down their order. A candidate is dropped when its IoU (computed as `yolov8_post` does) with an earlier kept candidate of its class is above 0.7. The first 300 kept are the result. The score sums are not used.
- **Ties (§6):** each rule also says whether its 301st detection has the 300th's score.
- **Against the board:** on the exploration's board dumps (21 images, YOLOv10n INT8), `yolov10_rule` gives `yolov10_post`'s lists at conf 0.25 and 0.001: all 42 with the same count, order and classes.
  - **Bit for bit:** all 32,530 scores and box coordinates are the board's, on the host and in the image. Before the decode above, 2,693 differed, by up to 6.2e-5 px; §2.3 allows 1e-6 in score and 1e-3 px in box.
  - **Test:** a test repeats this wherever the dumps are (they are not committed). It checks §2.3's tolerances, then every bit.
  - **Both rules against the board tool:** see "The board tool" below.

## The board tool (§2.2 to §2.5, §8 step 3)

[tools/m4_accuracy.cpp](../../tools/m4_accuracy.cpp) runs on the board, as root, from the repository. It has three modes.
- **`pass`, the INT8 pass:**
  - **Binding:** context 0 (NPU core 0) with M1's output bindings; the input is bound as B binds its slots (`CpuSlots`).
  - **Each image:** its PNG is decoded and made RGB in the slot. The slot's bytes must be the manifest's (SHA-256, [src/m4/sha256.cpp](../../src/m4/sha256.cpp)); otherwise the pass is invalid and stops (§5).
  - **Bound outputs:** on every 100th image (the 1st, the 101st, ...) they must equal `rknn_outputs_get` of a separate context on the same input (§2.5); otherwise the pass is invalid and stops.
  - **Dumps:** the first 50 images' outputs go to `<record>/dump/`, for §8 step 3's check of the rules, and their SHA-256 to `<record>/dump.sha256`, which is committed (the dumps are not). With `--compare-dump` they are also compared byte for byte with that check's dump, and the count of images that differ is reported (§2.5). Before the pass starts, that dump's files must be the ones its record's `dump.sha256` lists.
  - **mAP's rule** ([src/m4/map_rules.cpp](../../src/m4/map_rules.cpp)): `yolov8_map` or `yolov10_map`, conf 0.001, at most 300. It writes the detections files below, under `map/`.
  - **The pipeline's settings (§2.4)**, under `pipeline/`:
    - YOLOv8n goes through `NeonPost` and `yolov8_post`, both at conf 0.25 and NMS 0.45. They are compared field for field, and `NeonPost`'s detections are written.
    - YOLOv10n goes through `yolov10_post` at conf 0.25, at most 300.
  - **Records:** `<record>/pass.csv` holds the counts, the files' SHA-256 and `valid`; its header names the model, its SHA-256 and both post-processings (§4). `<record>/outputs.csv` holds the outputs' attributes, and `<record>/env.txt` the board's environment (`collect_env.sh`).
  - **A pass of kind `m4`:**
    - It runs every image as root, with the clocks fixed and freeze-m4's commit.
    - It compares its first 50 images with §8 step 3's dump (`--compare-dump`, `--dump` of at least 50) and deletes its inputs.
    - It needs the compiled objects whose SHA-256 its header records.
  - **Exit codes:** a run that cannot start exits 2. One exception: a model the runtime refuses ends the run in `NpuContexts::open` with exit 1 and no `pass.csv`. Any error once the first image has started makes the pass invalid, with the reason in `pass.csv`; the reference outputs included.
- **`decode`:** §8 step 3's check that every PNG decodes to the manifest's bytes.
- **`wiring`:** §8 step 3's check of the shared choice of post-processing (`PipelinePost`).
  - It runs on the first 10 frames of `tensors_c.u8` (A7's SHA-256 is checked).
  - **C10's setting:** `PipelinePost` must give the same detections as the pass's own call of `yolov10_post` at conf 0.25.
  - **C8's setting:** `PipelinePost` must give the same detections as `NeonPost` called directly.
  - **The settings:** the tool's own calls write them out (conf 0.25, NMS 0.45, at most 300) rather than take `PipelinePost`'s constants, so the check covers the settings as well as the dispatch.
  - **Detections:** they go to `wiring_detections.csv` (§8 step 3 reports them).
- **The C++ YOLOv8n rule (`yolov8_map`):**
  - **Candidates:** it takes them with `yolov10_post`'s `first_above`, as the Python does. `yolov8_post`'s `s > quantize(conf)` is not always the same test.
  - **Arithmetic:** its file is compiled without floating-point contraction, so the IoU is computed as written. The decoding's fused `acc += e × i` is written as `std::fma`, as in `yolov10_post`'s compiled code.
  - **Tests:** `test_core` covers the class-wise NMS, conf, the tie order, the 300 and their tie, and `max_nms`.
- **From the PC, [tools/m4_board_pass.py](../../tools/m4_board_pass.py):**
  - It sends the manifest before the tool starts. Then, while the tool runs, it sends the PNGs in chunks (500 by default, at most 3 on the board).
  - On the board, [tools/m4_board_run.sh](../../tools/m4_board_run.sh) runs the tool, detached from the ssh session. For a pass it first fixes the clocks and records the environment.
  - The tool deletes each chunk after its images (§2.2).
  - **Robustness:** every ssh call is retried with a back-off, so a dropped connection does not cost a pass.
  - **Watching the run:** by its PID and the board's boot id (`run.txt`). A board reset, a killed run or `--timeout-min` (240) end the wait, as an invalid run.
  - **Afterwards, whatever happened:** it fetches the record into `results/<kind>/` with its own account (`board_pass.txt`), and a pass's detections into `D:/m4data/passes/<record>/`.
  - **A run of kind `m4`** needs a clean tree whose code (`src`, `tools`, `tests`, `CMakeLists.txt`) is freeze-m4's, and the board built from the PC's HEAD. Its detections stay outside `results/` (§2.5).
  - **Commands:** `python tools/m4_board_pass.py pass --model yolov8n --kind check --name rules_yolov8n --limit 50 --dets-in-record`, `… decode --kind check --name m4_decode`, `… wiring --model yolov10n --kind check --name wiring_yolov10n`. The environment gives `SSH` and `BOARD_SUDO_PASSWORD`.
- **The rules' check, [tools/m4_rules_check.py](../../tools/m4_rules_check.py),** runs in the image through `pc_run.py`.
  - It runs the Python rule on a board record's dumps and compares it with that record's C++ lists, by §2.3's tolerances. The candidates' count and the tie flag must agree too.
  - **The exception of §2.3,** a difference caused by an IoU within 1e-6 of 0.7: the Python NMS must have compared such an IoU in that image, and moving the threshold by 1e-6 one way or the other must make the Python rule give the C++ list and tie. A difference in the candidates' count, which comes before the NMS, is never excepted ([tests](../../tests/test_m4_rules_check.py)).
  - It also reports how many values are equal bit for bit, and every IoU within 1e-6 of 0.7.
- **Trials of 2026-11-17** (kind `check`, the board built at `f0fcc7d`, records `results/check/20261117T04*_trial_*` and `…T05*_trial_*`):
  - **Rules on the first 50 images:**
    - YOLOv8n: all 50 lists equal; 74,996 of 75,000 values bit for bit, and the other 4 are 1 ulp apart (glibc's `expf`, above).
    - YOLOv10n: all 50 equal; all 75,000 values bit for bit.
    - No IoU came within 1e-6 of 0.7.
  - **Wiring:** 10 of 10 frames equal for both settings.
  - **Decoding:** 100 of 100 images were the manifest's.
  - **Determinism:** a second YOLOv8n run's dumps equal the first's for all 50 images.
  - **Pipeline settings:** `NeonPost` and `yolov8_post` agreed on all 50 images.
  - **Invalid runs:** a PNG swapped for another made the pass invalid at that image, with its reason and exit 1. Run kind `m4` without its conditions, run kind `check` with a freeze-m4 commit, `--limit 0`, and a file that is not one of M4's models were each refused with exit 2.
  - **The first trial** stopped at once (`…T045612Z`): the board's tree has no `results/`, so the script now sends the manifest.
  - **Objects:** `yolov8_post.cpp.o` and `yolov8_post_neon.cpp.o` have A13's SHA-256.
  - **Speed per image** (the waits for inputs timed apart after the review): decoding 22 ms, NPU 16 to 18 ms, mAP's rule 12 ms (YOLOv8n) or 6 ms (YOLOv10n). A pass of 5,000 images takes about 5 minutes, plus sending 2.6 GB of PNGs.
- **After the review** (the board built at `a2890a8`, records `results/check/20261117T053*_trial2_*`):
  - **Rules:** the same results on the first 50 images.
  - **Wiring:** 10 of 10 frames equal for both settings, with their detections recorded.
  - **Determinism:** the second YOLOv8n dump equals the first, after its files were checked against the first record's `dump.sha256`.
  - **A dump without `dump.sha256`** (the first trials') was refused as a dump to compare with, with exit 2.
- **The independent review** found nothing that changes the results. Its points are in: the driver's robustness, the m4 pass's conditions, the narrower exception, `m4_coco`'s check of the files' SHA-256, and the smaller items above.
- **A dependency to know:** `yolov10_post` fuses `acc += e × i` because GCC contracts by default in this build. The Python rule and `yolov8_map` assume it does. Were a flag or compiler to change that, the pinned objects' SHA-256 and the bit-for-bit YOLOv10n check would show it.
- **§8 step 3 at the code to be frozen:** [below](#8-step-3-at-the-code-to-be-frozen).

## Detections files

A pass writes two tables to a directory of its own, outside the repository: `D:/m4data/passes/<record>/`. Each has a `# key: value` header (`pc.json`'s fields, the pass, the model and its SHA-256, the precision, the rule, the manifest's SHA-256s).
- **`detections.tsv`:** `image_id, rank, class, score, x1, y1, x2, y2`.
  - **Order:** one row per detection, the images in the manifest's order, each image's detections in the rule's order.
  - **Values:** the score and the box (model input coordinates) are float32 printed with 9 significant digits, as the board's C++ prints them.
- **`images.tsv`:** `image_id, candidates, detections, tied`.
- **Both precisions:** the board's INT8 pass writes the same two files, under `map/` (mAP's rule) and `pipeline/` (§2.4, `candidates` -1: not counted). Its header also names the pass's record.

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
  - **`exit.txt`:** `pc_run.py` writes the tool's exit code and end time there when it ends, and `exit_code: none` when the script itself was stopped first.
- **Then, to the board:** `python tools/m4_send_record.py results/m4/<record>` copies the record into the board's `results/m4/`, where the sessions' gates look for the four passes (below). An invalid pass is sent too.
- **Scope:** a pass of kind `m4` runs all 5,000 images. `--limit N` (N ≥ 1) runs the first N, for a check only.
- **Speed:** on this PC, per image (5 images, onnxruntime plus the rule):
  - YOLOv8n: 0.87 s + 0.21 s, so a pass takes about 1.5 hours;
  - YOLOv10n: 0.70 s + 0.05 s, about 1 hour.
  - Other work on the PC slows it: with another project's build and a Windows update running, the 20-image checks below took 4.5 s and 2.1 s per image.
- **Checks of 2026-11-17 (`--limit 20`, kind `check`):** `results/check/20261117T021343Z_fp32_yolov8n_limit20` and `…T021553Z_fp32_yolov10n_limit20`, both `valid,1`, at commit `3251204`, before the review's changes to the decode. They checked the files and the speed. Their detections were not evaluated: AP is computed after freeze-m4 only.
- **Tests in the image:** `results/check/20261117T034320Z_m4_rules_tests` (11, the bit-for-bit board comparison included), `…T034351Z_m4_coco_tests` (11) and `…T034409Z_m4_fp32_tests` (6), at commit `d567e2c`. None was skipped. The runs at `3251204` (`…T021046Z`, `…T021205Z`, `…T021233Z`) came before the review.

## §8 step 3 at the code to be frozen

Run on 2026-11-17, kind `check`, with the board built at `63febdf` (#55's merge).
- **The code to be frozen:** after `63febdf` come only [tools/m4_checks.py](../../tools/m4_checks.py), which reads records, its tests, a fix to another test, and notes. `src/`, `CMakeLists.txt` and the tools these runs used are unchanged. The verdicts list what changed between the build and HEAD. A14 names the commit.
- **The data check** is ①'s record `20261116T064917Z_m4_inputs`, made at `9dbf814`. `tools/m4_prep.py` and the evaluation image are unchanged since.
- **Commands:**
  - **On the PC:**
    - `python tools/m4_board_pass.py decode --kind check --name m4_decode`;
    - `… pass --model <model> --kind check --name rules_<model> --limit 50 --dets-in-record`;
    - `… wiring --model <model> --kind check --name wiring_<model>`;
    - `MSYS_NO_PATHCONV=1 python tools/eval/pc_run.py --kind check --name rules_check_<model> -- tools/m4_rules_check.py --board-record results/check/<rules record>`.
  - **On the board, as root:** the default path and the arms check, as in [comparison.md](comparison.md) (M4). The arms check ran at 25 °C with the heatsink and fan.
  - **The verdicts,** on the PC with the arms session fetched: `python tools/m4_checks.py --inputs … --decode … --rules-check <v8> <v10> --wiring <v8> <v10> --default … --arms …`. All passed (exit 0).
    - Besides each check, it requires every record to be of run kind `check`, every board tool to have exited 0, and every board and PC record to name one build. The PC's code must have been clean, on the evaluation image of `tools/eval/image_id.txt`.
    - The data record may be older than the build: its `tools/m4_prep.py` and image must be the build's.
- **Results** (records in `results/check/`):

| Check | Records | Result |
|---|---|---|
| Data (§2.1, §2.2) | `20261116T064917Z_m4_inputs` | COCO's two zips have `coco.sha256`'s SHA-256. 5,000 images, each at its annotated size, and the 20 quantization images by name and SHA-256. Manifest `b29adf50…` |
| Decoding | `20261117T081135Z_m4_decode` | 5,000 of 5,000 PNGs decoded to the manifest's bytes, in 167 s |
| Rules, YOLOv8n | `…081427Z_rules_yolov8n`, `…081555Z_rules_check_yolov8n` | 50 of 50 lists equal. 74,996 of 75,000 values bit for bit; largest box difference 3.05e-5 px. No IoU within 1e-6 of 0.7 |
| Rules, YOLOv10n | `…081441Z_rules_yolov10n`, `…081622Z_rules_check_yolov10n` | 50 of 50 lists equal. 75,000 of 75,000 values bit for bit. No IoU within 1e-6 of 0.7 |
| Wiring, C8 | `…081456Z_wiring_yolov8n` | 10 of 10 frames equal (`PipelinePost` neon against `NeonPost` called directly) |
| Wiring, C10 | `…081504Z_wiring_yolov10n` | 10 of 10 frames equal (`PipelinePost` v10 against the pass's call of `yolov10_post`) |
| Default path | `…081656Z_m4_default_c` | the threads, affinities and queues of M1's `32_main_C_2ch_r1` |
| Arms | `20261117T082036Z_m4_check` | both runs valid. Every frame follows the sampler's rule, and each `env.json` records the arm's model and post-processing |
| Build | the 20 places the records name it | all name `63febdf`; the data record's `9dbf814` has its `m4_prep.py` and image |
| Objects | the 7 records that name them | the same four objects in all. `yolov8_post`'s and Cp's are A13's |

- **The arms' values** (8 channels, main condition, staggered):

| Arm | Run | Process CPU (s) | Stagger error (ms) | Phase spread (ms) |
|---|---|---|---|---|
| C8 | `01_m4_check_C8_8ch` | 17.560 | 7.4 | 159.9 |
| C10 | `02_m4_check_C10_8ch` | 17.810 | 17.0 | 136.9 |

  - Their saturation labels are `unknown`: no `calib_npu` session of kind `m4` exists yet, so rule 3 does not judge these runs.
- **The objects, for A14:**
  - `yolov8_post.cpp.o`: `536b9351…` (A13's);
  - `yolov8_post_neon.cpp.o`: `3956c1ba…` (A13's);
  - `yolov10_post.cpp.o`: `b7642a3b11681ac79c4d0f0bb494f04988eb444bfdc9361f195983560f7cbeb8`;
  - `pipeline_post.cpp.o`: `28a471ca0bfa158e34d320c05c6d68950e665b62c9c43d38f7d03baec9c9a14a`;
  - the YOLOv8n rule, `m4map.dir/src/m4/map_rules.cpp.o`: `94f5f4316f0505c5b1ac0d298258d578c910f697b475c112712a89dec38be35e`.
- **The dumps for §2.5:**
  - `20261117T081427Z_rules_yolov8n/dump` (`dump.sha256` `74e48290…`) and `…081441Z_rules_yolov10n/dump` (`b7b4f93a…`).
  - They are kept on the PC and on the board, not committed.
  - Their `dump.sha256` equal those of the trials at `a2890a8`. So the outputs are the same across the two builds.
- **Still before `freeze-m4`:** the memory check at 5,000 images (Evaluation, below), then A14 with these results.

## The order of the runs (§8 step 5)

The four passes come before the `calib_npu` session, and that before the pipeline sessions. [tools/m4_order.py](../../tools/m4_order.py) reads the passes from their records ([tests](../../tests/test_m4_order.py)).
- **The records:**
  - **A board pass:** `run.txt` gives the command, the start, and the PIDs of `m4_board_run.sh` and of the tool. Once the tool ends, `exit_code` and `pass.csv` follow. A TERM or INT to the script stops the tool too.
  - **An FP32 pass:** `pc.json` names `tools/m4_fp32.py` and its container, `m4-<record>`. Once the tool ends, `exit.txt` and `pass.csv` follow.
  - **Not an attempt:** a run that did not start, i.e. exit code 2 without `pass.csv` (the tool refused its options, or `m4_board_run.sh`'s `set_perf_mode.sh` or `collect_env.sh` failed), or docker's own errors, 125 to 127, on the PC.
  - **An invalid attempt:** a run that ended without `pass.csv` any other way (§5: the tool exits abnormally), such as a board reset, a killed run or the runtime refusing the model.
  - **The end:** the later of the tool's own (`pass.csv`'s start and seconds) and the one its runner recorded.
- **Done:** a pass is done when it is valid, or once it has been invalid twice, as §5 has it: it is then reported as missing. A board run that still runs in this boot holds the passes back: its script, or its tool, is alive under its PID.
- **Amendments:** the gates know nothing of them. They count the passes of `freeze-m4`'s code, and a rerun under an amendment (§9) follows that amendment's own procedure.
- **Where it is checked:**
  - **On the board, before a session starts:** `tools/stream_session.py` for the pipeline sessions, and `tools/m4_order.py gate` for `tools/m4_calib.sh`.
    - Both refuse while any `build/m4_accuracy` runs, whatever its record says.
    - Both also need a passed wiring check of the build's four post-processing objects, for both settings (§8 step 3, §5).
  - **The board has the INT8 passes' records already.** The FP32 ones come from the PC with `tools/m4_send_record.py` ([tests](../../tests/test_m4_send_record.py)).
    - It sends a record only once the pass has ended.
    - It unpacks the record beside its place and then moves it in, so a copy cut short leaves nothing there.
    - It refuses to overwrite a different record of the same name, and checks every file's SHA-256 on the board.
    - **A pass whose `pc_run.py` died** (a crash, a reboot) has no `exit.txt`. `--ended` writes one, with `exit_code: none` and the time then, once docker shows that its container does not run.
  - **The `calib_npu` session that decides** must also have started once the passes were done ([comparison.md](comparison.md)).
  - **Afterwards, on the PC:** `tools/check_prereg_order.py` reports every calib_npu or pipeline session in `results/m4/` that starts before the passes that started before it were done and had ended. It also reports a pipeline session that starts before the last record of its `calib_npu` session.
- **Trials of 2026-11-17** on the board (built at `bad15c9`, then `eeaa97d`), with nothing written under `results/m4/`:
  - **Before `freeze-m4`:** the gate and `RUN_KIND=m4 tools/m4_calib.sh` refused, and the script made nothing.
  - **Wiring:** the build's four objects had the SHA-256 of the board tool's trials, and the wiring gate took their `trial2` wiring records.
  - **The changed `m4_board_run.sh`:** a wiring check (`results/check/20261117T074956Z_trial3_wiring_yolov10n`) gave 10 of 10 frames equal, exit 0, and `tool_pid` in `run.txt`.
  - **Killing the script:** a TERM to the script alone, during a run, stopped its tool too. `exit_code` became 143, and both the alive check and the scan of running tools then found nothing.
  - **`m4_send_record.py`:** a trial copy of an FP32 record, sent to a trial directory, arrived as root's 0644 files with equal SHA-256. A second send changed nothing, and a record of kind `check` was refused.
  - **Real formats:** the board tool's and the PC's trial records, relabelled as kind `m4`, read as four valid passes with their ends.

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
  - **The files themselves:** for a model's pass, `detections.tsv` and `images.tsv` must have the SHA-256 that the pass's record gives. The board's INT8 pass gives one pair for `map/` and one for `pipeline/`, the PC's FP32 pass one pair. A file truncated on its way back from a Release is refused.
- **Size:** a pass has about 1.5 million detections. Most of the memory goes to two places:
  - COCOeval's records per image, category and area range (`evaluate`);
  - the loaded results (`loadRes`).
- **Saving memory:** the tool frees the detections table before COCOeval, and loads the results once for both image sets.
- **Measured:** synthetic detections (random boxes and classes, 300 per image; no model's), in the image:
  - 500 images: peak 0.64 GB;
  - 2,500 images: peak 2.5 GB, exit 0 at 6 GB without swap;
  - growth is linear, so 5,000 images need about 4.9 GB, under the 6 GB limit.
- **A 2 GB limit is too small:** with 2 GB plus 2 GB of swap, 5,000 images were killed for memory after 34 minutes of swapping.
- **The check at 5,000 images, [tools/eval/m4_coco_memory.py](../../tools/eval/m4_coco_memory.py):** it writes synthetic detections for the manifest's first N images and runs `tools/m4_coco.py` as `pc_run.py` does, under the same 6 GB without swap. It reports the time and the peak memory.
  - **Another PC can run it:** it is a check of the tool, not a measurement of M4, and the peak memory depends on the image and the data, not on the PC. The PC needs to be amd64, run Docker, and give the container 6 GB (a Docker VM of at least 7 GB).
  - **What it needs:** the repository, the image's archive (`D:/m4data/images/m4-eval_c676e76f95356ea1.tar`, loaded with `docker load` and checked by its ID) and `annotations_trainval2017.zip`. `m4_coco.py` checks the zip against `coco.sha256`. The COCO images are not needed.
  - **How to run it:** the command is in the tool's docstring. The summary goes to `m4_coco_memory.txt` in the work directory, and it names the PC.

## Files

| File | Content |
|---|---|
| `tools/m4_rules.py` | the two rules in Python ([tests](../../tests/test_m4_rules.py)) |
| `tools/m4_fp32.py` | the PC's FP32 pass ([tests](../../tests/test_m4_fp32.py)) |
| `tools/m4_coco.py` | the detections files, their COCO conversion and their evaluation ([tests](../../tests/test_m4_coco.py)) |
| `tools/eval/m4_coco_memory.py` | the evaluation's time and peak memory on synthetic detections |
| `tools/m4_accuracy.cpp` | the board tool: INT8 passes, the decoding check and the wiring check |
| `src/m4/map_rules.cpp`, `sha256.cpp` | the C++ mAP rules ([tests](../../tests/test_core.cpp)) and SHA-256 in memory |
| `tools/m4_board_pass.py`, `tools/m4_board_run.sh` | the board tool's runs from the PC: inputs in chunks, records back |
| `tools/m4_rules_check.py` | §8 step 3's comparison of the two implementations of each rule |
| `tools/m4_order.py` | the passes and the wiring check as their records show them, and the `calib_npu` session's gate (§8 steps 3 and 5) |
| `tools/m4_checks.py` | the verdicts of §8 step 3's checks, from their records ([tests](../../tests/test_m4_checks.py)) |
| `tools/m4_send_record.py` | an FP32 pass's record, copied to the board for the gates |
| `results/check/<UTC>_trial_*/` | the board tool's trials of 2026-11-17 (their dumps are not committed) |
| `tools/eval/Dockerfile`, `requirements.txt`, `constraints.txt`, `image_id.txt`, `build.sh`, `save_image.sh` | the evaluation image, its ID, its checks and its archive |
| `tools/eval/fetch_coco.sh`, `coco.sha256` | COCO's two files and their SHA-256 |
| `tools/eval/pc_run.py` | a run on the PC after §4's checks ([tests](../../tests/test_pc_run.py)) |
| `tools/m4_prep.py` | the inputs and the data checks ([tests](../../tests/test_m4_prep.py); those with OpenCV run in the image) |
| `results/check/<UTC>_m4_eval_image/`, `…_m4_eval_image_archive/` | the image's checks, and its archive's SHA-256 |
| `results/check/<UTC>_m4_inputs/` | `pc.json`, `manifest.tsv`, `m4_inputs.csv`, `m4_prep.log` |
| `results/check/<UTC>_m4_{rules,coco,fp32}_tests/` | `pc.json` and the tests' log, in the image |
| `results/check/<UTC>_fp32_<model>_limit20/` | `pc.json`, `pass.csv`, `m4_fp32.log` of a 20-image check pass |
