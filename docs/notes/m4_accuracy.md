[한국어](m4_accuracy.ko.md)

# M4's accuracy: tools and procedure

How M4's accuracy (prereg/M4.md §2) is run.
- **Normative:** [prereg/M4.md](../../prereg/M4.md). This note says how the tools apply it.
- **Covered so far:** the evaluation image, COCO's files and the inputs.
- **Not yet:** the board's pass, the PC's passes and the evaluation. They are added here with their tools.

## The evaluation image (§8 step 2)

- **[tools/eval/Dockerfile](../../tools/eval/Dockerfile):** the converter image, unchanged, plus `pycocotools` 2.0.11.
  - **The converter image:** built from [tools/model/Dockerfile](../../tools/model/Dockerfile) on `python:3.10-slim-bookworm@sha256:5be5aaec…`. Its image ID is `sha256:3ea3796529342281fdb543b8911261112e5ab942798c2fea4363edf3eca67f8f`, and it converted both models.
  - **pycocotools:** pinned by its wheel's SHA-256 ([requirements.txt](../../tools/eval/requirements.txt)). It needs only numpy, which the base has; `pip check` finds nothing broken.
- **Packages:** Python 3.10, numpy 1.26.4, opencv-python 4.11.0.86, onnxruntime 1.23.2, pycocotools 2.0.11.
- **The image:** `sha256:c676e76f95356ea1aac39a87694ac5a498207890922f2a01c9fdbedd773f250a`, created 2026-11-16T05:31:15Z.
  - **Fixed in the repository:** the ID is in [tools/eval/image_id.txt](../../tools/eval/image_id.txt). `build.sh` and `pc_run.py` refuse any other image, and the runs use the image by this ID.
  - **The ID itself:** Docker Desktop's containerd image store makes it the image's content digest (its `RepoDigests`).
  - **An earlier build:** one build that day printed `sha256:e23dd324…`. It came before `build.sh` stopped rebuilding.
- **Build and checks:** `MSYS_NO_PATHCONV=1 tools/eval/build.sh`.
  - **Once:** it builds the image only if it is missing. A rebuild may give a new ID; `--rebuild` forces one, and its ID must then go to `image_id.txt` by a commit.
  - **What it checks:**
    - the base image's ID, and that the image's layers start with the base's;
    - the image's ID against `image_id.txt`;
    - that `pip freeze` equals [constraints.txt](../../tools/eval/constraints.txt), the converter's plus pycocotools;
    - `pip check`, and that pycocotools' COCOeval imports.
  - **Record:** `results/check/<UTC>_m4_eval_image/image.txt`. The run of 2026-11-16T06:44:30Z passed every check.
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
  - **the image:** its ID against `image_id.txt`;
  - **the CPU model;**
  - **the host clock:** its offset from NTP, from `w32tm /stripchart /computer:time.windows.com /samples:3 /dataonly`. The samples that answer count, and the others are recorded;
  - **the clock's sync state:** `w32tm /query /status`;
  - **the container clock:** its offset from the host's, from the shortest of 5 exchanges over one container.
- **Refusal:** the tool does not start with no NTP answer, or with either offset above 1 s.
- **The run:**
  - **Record:** `results/<kind>/<UTC>_<name>/`. Its `pc.json` holds the above, the start time from the host clock, the run kind, M4's stage commits and the amendment in force (PROTOCOL §1.4, M4 §4). So even a tool that stops early leaves a header that `tools/check_prereg_order.py` checks.
  - **Container:** run by the image's ID. The repository is mounted read-only at `/repo`, the record at `/record`, and the data at `/data`. It has no network and no new privileges.
  - **Limits:** 2 GB of memory (4 GB with swap), as for the conversion. Other projects' containers share the Docker VM.
  - **Python:** `python3 -I`, since the inputs are downloaded data.
  - **`--checks-only`:** records `pc.json` and runs no tool, to record the PC's clock, say.
- **The PC:** Intel Core i7-6500U.
- **Its clock on 2026-11-16:**
  - **Until about 06:14 UTC:** the Windows Time service was not running. `w32tm /query /status` failed with `0x80070426`, which the inputs run's `pc.json` records. The stripchart samples that answered gave +0.75 to +0.76 s, within §4's 1 s, but nothing corrected the clock.
  - **At about 06:14 UTC, as the user reported:** the user set the service to start automatically, pointed it at `time.windows.com`, and resynchronized. Right after, the stripchart still gave +0.78 s.
  - **Why it may persist:** below 1 s, a standalone Windows PC's defaults correct the clock gradually rather than in one step. `--checks-only` records the clock, and it is measured again before the PC's passes.

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
  - **The rerun:** RESULT_RERUN

## Files

| File | Content |
|---|---|
| `tools/eval/Dockerfile`, `requirements.txt`, `constraints.txt`, `image_id.txt`, `build.sh`, `save_image.sh` | the evaluation image, its ID, its checks and its archive |
| `tools/eval/fetch_coco.sh`, `coco.sha256` | COCO's two files and their SHA-256 |
| `tools/eval/pc_run.py` | a run on the PC after §4's checks ([tests](../../tests/test_pc_run.py)) |
| `tools/m4_prep.py` | the inputs and the data checks ([tests](../../tests/test_m4_prep.py); those with OpenCV run in the image) |
| `results/check/<UTC>_m4_eval_image/`, `…_m4_eval_image_archive/` | the image's checks, and its archive's SHA-256 |
| `results/check/<UTC>_m4_inputs/` | `pc.json`, `manifest.tsv`, `m4_inputs.csv`, `m4_prep.log` |
