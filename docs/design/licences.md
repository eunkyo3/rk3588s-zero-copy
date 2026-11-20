[한국어](licences.ko.md)

# Licences of what the package would touch, and "bundle or fetch"

**Status:** step 1 of [packaging.md](packaging.md). The licence texts were read on 2026-11-19, at the versions this repository pins. This is a reading by the project, not legal advice; a commercial use should have it checked.

**Intended use (the owner, 2026-11-19):** an open-source portfolio project, not a commercial product. The decisions below do not depend on that: the limits on redistributing Rockchip's files hold for any distributor, and keeping the model outside keeps the package Apache 2.0.

**Result:**

| Part | Licence, as read | Decision |
|---|---|---|
| librknnrt 2.3.2 (binary) | Rockchip's own; it grants no right to redistribute | **fetch at install**, never bundle |
| RKNN headers (`rknn_api.h`) | proprietary notice; no copying without written permission | **fetch at build**, never bundle, not in the public header |
| ffmpeg-rockchip, as built here | LGPL v3 or later | **bundle** as separate shared libraries, with the notices and the source |
| MPP userspace | Apache 2.0 or MIT | not bundled; the image's |
| librga | Apache 2.0 | not bundled; the image's |
| The models (`.rknn`) | AGPL 3.0 | **not in the package**; fetched by the user's choice, or the user's own |
| This repository | Apache 2.0 | — |

Two things are new against the proposal: the RKNN **headers** are restricted too, and the **models' AGPL** matters more to a user than either library.

## librknnrt and its headers: fetch

- **The repository's licence** ([rknn-toolkit2 v2.3.2, `LICENSE`](https://github.com/airockchip/rknn-toolkit2/blob/v2.3.2/LICENSE)) is a copyright statement and a warranty disclaimer. It says "All rights reserved" and names no permission to copy, modify or redistribute.
- **The header** `rknn_api.h` (same tag) says its content is "confidential" and that "no part of this work may be disclosed, reproduced, copied, transmitted, or used in any way for any purpose, without the express written permission of Rockchip Corporation".
- **What Rockchip does:** it publishes both files in a public repository for download. That shows how to obtain them; it is not a licence to pass them on.
- **So:**
  - the package downloads `librknnrt.so` from Rockchip's repository at install, by its tag and SHA-256, as `scripts/setup_board.sh` does now;
  - no RKNN header is copied into this repository or into the package. This repository holds none today; `tools/setup/fetch_rknn_headers.sh` downloads them to `/opt/rknpu2/include` at build time;
  - the library's public header exposes no RKNN type, as the proposal already wanted for another reason.
- **Costs of fetching:** the install needs the network and GitHub, and breaks if Rockchip moves or removes the tag. The SHA-256 turns a changed file into a clear failure. An offline install would need the user to place the file by hand.
- **To bundle instead,** written permission from Rockchip would be needed. Nobody has asked.

## ffmpeg-rockchip: bundle, as LGPL v3

- **The licence of this build:** FFmpeg's `configure` at the pinned commit (`8b4385e9`) lists `rkmpp` and `rkrga` under `EXTERNAL_LIBRARY_VERSION3_LIST`, so they need `--enable-version3`. With that flag and without `--enable-gpl` or `--enable-nonfree`, `configure` sets the licence to "LGPL version 3 or later". That is the build of `tools/setup/build_ffmpeg_rockchip.sh`.
- **Why version 3:** MPP and librga are Apache 2.0, which FFmpeg's `LICENSE.md` calls incompatible with LGPL v2.1 but not with v3.
- **The Rockchip-specific files** (`libavcodec/rkmppdec.c`, `libavfilter/rkrga_common.c`) carry FFmpeg's usual LGPL v2.1-or-later header.
- **Linking from Apache 2.0 code:** the program and library here link FFmpeg dynamically. That is the form the LGPL is written for.
- **What the package must carry to bundle the four libraries** (libavcodec, libavformat, libavutil, libswresample):
  1. the texts of LGPL v3 and GPL v3 (`COPYING.LGPLv3`, `COPYING.GPLv3`), and a notice that the package uses FFmpeg under LGPL v3;
  2. the exact source: the commit, the `configure` line, and the build script. The simplest safe form is a source archive of that commit as an asset of the same Release as the `.deb`, not only a link to someone else's repository;
  3. the libraries as separate `.so` files that the user can replace with their own build. `/opt/rkzc/lib` with an rpath does this.
- **What the build links from Ubuntu:** zlib, liblzma, libX11/libxcb and libdrm, found by `configure` automatically and linked dynamically. (As packaged in step 5: the four bundled libraries need zlib, liblzma and libdrm; libX11 and libxcb are needed only by FFmpeg's parts that are not bundled.) They stay Ubuntu's packages (`Depends:` of the `.deb`), not bundled. A leaner `configure` line would drop some, but it would no longer be the measured build.
- **Building at install instead** (6 minutes on the board) avoids distributing FFmpeg binaries at all. It is the fallback, not the choice: it needs a compiler on the user's board and makes the install slow and fragile.

## MPP and librga: the image's

- **MPP** ([rockchip-linux/mpp](https://github.com/rockchip-linux/mpp), `LICENSES/`): Apache 2.0 and MIT.
- **librga** ([airockchip/librga](https://github.com/airockchip/librga), `COPYING`): Apache 2.0.
- Both come with FriendlyElec's image, and the package only depends on them. Their licences would allow bundling, but the kernel drivers they talk to are the image's, so bundling other versions would add risk without a gain.
- I read the upstream repositories' licences, not the exact files in FriendlyElec's image.

## The models: AGPL 3.0, and outside the package

- **YOLOv8n** derives from Ultralytics YOLOv8, and **YOLOv10n** from THU-MIG's YOLOv10. Both repositories' `LICENSE` is the GNU AGPL v3. This repository already distributes its model files under AGPL 3.0, in Releases, not in the tree ([models/README.md](../../models/README.md)).
- **In the package: no model.** The `.deb` stays Apache 2.0 plus the LGPL libraries. A separate, explicit command fetches a model from this repository's Release, and shows its licence.
- **What it means for a user:**
  - personal or research use on one's own board: no practical burden;
  - offering a service over a network, or shipping a product, with these models: the AGPL's source obligations apply to the model and, by Ultralytics' reading, to the software built around it. Ultralytics sells a separate licence for that case;
  - a user who cannot accept the AGPL needs a model with another licence. That is open question 2 of the proposal (any `.rknn` with the user's post-processing), and it now has a licence reason besides the technical one.
- **This repository's own code** for decoding boxes follows rknn_model_zoo v2.3.2, which is Apache 2.0. Whether running an AGPL model makes the program around it AGPL is disputed, and this note does not settle it.

## What changes in the proposal

- **Dependency table:** librknnrt has one option, fetch. ffmpeg-rockchip is bundled, with the three items above.
- **A new file in the package:** `/usr/share/doc/rkzc/copyright`, listing each part, its licence and where its source is.
- **The package's promise** gains one line: it installs nothing of Rockchip's that it did not download from Rockchip, and no model.
- **Step 5** (the packaging script) gains the source archive of ffmpeg-rockchip as a Release asset.

## What was not checked

- The licences of FriendlyElec's image itself, and of the kernel drivers in it. The package does not distribute them.
- The licence of each Ubuntu package the `.deb` would depend on; they are installed by apt from Ubuntu.
- Patents on H.264 decoding. The decoder is the board's hardware; a product would still need its own look.
- Any written terms from Rockchip beyond the two files quoted. If Rockchip publishes or grants a redistribution licence, "fetch" can become "bundle".
