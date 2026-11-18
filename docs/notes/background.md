[한국어](background.ko.md)

# Background: existing Rockchip pipelines and measurements

What other sources say about feeding decoded video frames to the RKNN NPU on the RK3588 family, which throughput figures they publish, and whether the runtime behaviours this project ran into are documented. It answers issue #72.

- **Sources:** primary only: Rockchip's and airockchip's repositories and documents, Rockchip's RK3588S datasheet, ffmpeg-rockchip and GStreamer's documentation. They are listed under [Sources](#sources). GitHub links are pinned to a tag or a commit.
- **Versions:** rknn-toolkit2 and rknn_model_zoo `v2.3.2`, as this project uses ([env.md](env.md)). librga has no tag for the board's 1.10.3. The note cites the tag `v1.10.0`, and once a later commit. ffmpeg-rockchip at `8b4385e`, the commit this project builds.
- **Read on 2026-11-18.** Pages without a version can change.
- **Kept apart:** each section gives what the sources say first, then what this project did, with links to its notes. The note does not rank this project's numbers against others'.
- **Not registered.** Nothing here changes a registered value or a verdict.

## Feeding decoded frames to the NPU

### RKNN's zero-copy API

What the sources say:
- **Two API groups** ([User Guide][ug] §1.1.3, §5.2.1):
  - The general API (`rknn_inputs_set`, `rknn_outputs_get`, §3.3) copies the input into the NPU's memory for every frame. Normalisation, quantisation, layout conversion and dequantisation run on the CPU; normalisation and quantisation move to the NPU when the input meets the zero-copy alignment rules.
  - The zero-copy API uses memory allocated beforehand, "including memory created by NPU or other frame, such as DRM". Normalisation, quantisation and inference run on the NPU. "The data layout conversion and dequantization of the NPU output are executed on the CPU or NPU."
  - The guide's example: "decoded data can be directly imported into the NPU by zero-copy API".
  - Input with only a virtual address can use only the general API. Input with a physical address or an fd can use both.
  - "General API and zero-copy API interface cannot be called together."
- **Three calling patterns** (§5.2.2):
  - the runtime allocates input and output (`rknn_create_mem`);
  - the caller allocates them (`rknn_create_mem_from_fd`, `rknn_create_mem_from_phys`);
  - the caller also allocates weights and internal memory (`rknn_set_weight_mem`, `rknn_set_internal_mem`).
  - `rknn_set_io_mem` binds a memory object. It is called once, then `rknn_run` runs repeatedly (§5.2.3).
- **Native layouts** (§5.2.3):
  - Inputs and outputs are queried with `RKNN_QUERY_NATIVE_INPUT_ATTR` and `RKNN_QUERY_NATIVE_OUTPUT_ATTR`, which give "the optimal hardware efficiency".
  - Table 5-6: for int8 models on RK3588, 4-dimensional zero-copy outputs can be NCHW, NC1HWC2 or NHWC.
  - An NC1HWC2 output that the user needs as NCHW is converted "externally".
- **Rockchip's examples:**
  - rknn-toolkit2's [`rknn_zero_copy` README][tk2-zc] lists four demos in `rknpu2/examples/rknn_api_demo`. [`rknn_create_mem_demo`][tk2-mem] copies the input into a `rknn_create_mem` buffer with `memcpy`. [`rknn_create_mem_with_rga_demo`][tk2-rga] has RGA (`imresize`) write into that buffer's fd. The other two allocate with the MMZ library and register through `rknn_create_mem_from_fd` and `rknn_create_mem_from_phys`.
  - rknn_model_zoo's [`yolov8_zero_copy.cc`][mz-zc]: RGA letterboxes into the fd of a `rknn_create_mem` input bound as UINT8. Its [`image_utils.c`][mz-img] falls back to the CPU when a width is not 16-aligned. The outputs are bound with their native attributes, and the example converts NC1HWC2 to NCHW on the CPU itself (`NC1HWC2_i8_to_NCHW_i8`).
  - The examples read here take a still image from a file. None takes a frame from a video decoder.

This project:
- **C** is the caller-allocated pattern: RGA writes from the decoder's DRM PRIME dma-buf into a dma-heap slot registered with `rknn_create_mem_from_fd` ([spike.md](spike.md), [AMENDMENTS A1](../../prereg/AMENDMENTS.md)).
- **B** copies with `memcpy` into an input from `rknn_create_mem`, bound with `rknn_set_io_mem` (A3).
- **Both** bind their outputs as NCHW INT8, not in the native layout ([below](#runtime-behaviours-this-project-ran-into)).

### ffmpeg-rockchip's RGA filters

What the sources say, at commit `8b4385e`:
- **The project:** its [README][ffr-readme] calls it "an UNOFFICIAL fork of FFmpeg", not Rockchip's property.
  - It has MPP decoders and encoders, and three RGA filters: `scale_rkrga` ("video resizer and format converter"), `vpp_rkrga` (scale, crop, transpose) and `overlay_rkrga` (compositor).
  - It lists "Zero-copy DMA in above stages".
  - For maximum resolution and frame rate it refers to "the datasheet or TRM".
- **`scale_rkrga`** ([vf_vpp_rkrga.c][ffr-vpp], [rkrga_common.c][ffr-common]):
  - It takes and returns DRM PRIME frames. It writes into frames from a pool the filter allocates itself.
  - Its options include `w`, `h`, `format`, `force_original_aspect_ratio` and `core`, which picks RGA3 or RGA2 cores. RGB24 is among its formats.
  - This note found no padding option, so a letterbox needs another step.
- **DMA32:** its MPP device context allocates with `MPP_BUFFER_FLAGS_DMA32` unless the device option `dma32=0` is given ([hwcontext_rkmpp.c][ffr-hwctx]).

This project:
- It uses the fork's `h264_rkmpp` decoder ([env.md](env.md#decoder)). It calls librga itself, so that RGA writes straight into a slot the NPU reads.
- PROTOCOL §5.4 named `scale_rkrga` as C's last fallback. A1 recorded that no fallback was needed, so `scale_rkrga` was not tried.

### GStreamer

What the sources say:
- **GStreamer:** its [plugin documentation][gst-doc] lists no element for Rockchip's MPP or RGA.
- **Rockchip's plugin** is published in the `gstreamer-rockchip` branch of [JeffyCN/mirrors][gst-tree].
  - Most of the branch's commits come from rock-chips.com addresses. The latest come from other contributors.
  - The `rockchip-linux` GitHub organisation has no repository of that name.
- **At commit `bff0659`:**
  - The plugin `rockchipmpp` registers the decoder `mppvideodec`, a JPEG decoder, and H.264, H.265, VP8 and JPEG encoders ([gstmpp.c][gst-mpp]).
  - There is no separate RGA element. The decoders scale, crop and rotate (`width`, `height`, `crop-rectangle`, `rotation`), and `mppvideodec` converts the pixel format (`format`) ([gstmppdec.c][gst-dec], [gstmppvideodec.c][gst-vdec]).
  - These conversions try librga first, then the GPU, when the plugin is built with librga (its `rga` option, [meson_options.txt][gst-opt]).
  - The plugin has no element for the NPU.

This project does not use GStreamer.

## Published measurements

| Source | Figure | Conditions given | Not given |
|---|---|---|---|
| [RK3588S datasheet][ds] Rev 1.5, §1.2.4 | H.264 decoding "8K@30fps (7680x4320)"; H.265 and VP9 8K@60fps | codec, profile, level, resolution, frame rate; "Multi-channel decoder in parallel for less resolution" | bit rate; how many lower-resolution streams |
| Same, §1.2.6 | NPU "up to 6 TOPS", three cores | none | data type, clock, model |
| [librga guide][rga-guide] v1.10.0, "Design Index" | RK3588's RGA3: 3 pixels/cycle "by pass", 2 "scale"; its RGA2-Enhance: 2 | "theoretical data"; real speed depends on bandwidth and clock | RGA clock on RK3588; measured times |
| [rknn_model_zoo README][mz-readme] v2.3.2, "Model performance benchmark(FPS)" | RK3588, one core: YOLOv8n 73.5 FPS, YOLOv10n 61.2 FPS | INT8; input [1, 3, 640, 640]; "maximum NPU frequency"; inference only, without pre- and post-processing "if not specified" | board, runtime version, timing method, CPU and DDR clocks |

- **librga's measured times** ([FAQ][rga-faq] Q1.1) are for an RK3566 (RGA2) on Android 11, copying only. Its formula example uses 4 pixels/cycle for RGA3, where the Design Index gives 3 and 2.
- **The model zoo** says its demos are verified with RKNPU2 SDK ≥ 2.3.2. The table does not say which runtime measured it.
- **Not found:** an RK3588 throughput figure in MPP's [developer guide][mpp-guide] (`1.1.0`); a channel count for parallel decoding in the datasheet.

This project's measurements, under its own conditions:
- **Decoder:** 32 channels of 1080p30 H.264 decoded in real time, the cap of the procedure ([calibration.md](calibration.md#l-results)). At 4K30, a short decode-only check kept up with 10 channels, not with 12 ([AMENDMENTS A11](../../prereg/AMENDMENTS.md)).
- **RGA:** 1.80 ms per frame for C's letterbox of a 1080p NV12 frame, synchronous, one channel, default governors ([spike.md](spike.md)).
- **NPU:** YOLOv8n INT8 on one core, 70.73 inferences/s at fixed clocks, each step binding its input with `rknn_set_io_mem` ([calibration.md](calibration.md#p_cap-and-the-raw-npu-limit)). M4's session measured YOLOv8n and YOLOv10n again ([M4.md](../report/M4.md#npu-capacity)).

Whether the conditions are comparable:
- **NPU:** the model zoo's rows and this project's one-core rates share the model name, input size, INT8 and one core. The zoo's table does not give its board, runtime, clocks or timing method. So it is not known whether they are comparable.
- **Decoder:** the datasheet gives one stream's maximum per codec. This project counts parallel 1080p and 4K streams. They are not comparable.
- **RGA:** the Design Index gives theoretical pixels per cycle, with no clock. The spike's time includes the call and its synchronisation. They are not comparable.

## Runtime behaviours this project ran into

| Behaviour, in this project | Documented elsewhere? |
|---|---|
| **DMA32 heaps.** The decoder allocates from `system-dma32`; C's slots come from `system-uncached-dma32` (A1, [spike.md](spike.md)). The project used only DMA32 heaps and did not test memory above 4 GiB. | **Yes, for RGA.** librga's [guide][rga-guide]: RK3588's RGA2 has a 32-bit IOMMU, its RGA3 a 40-bit one, and "RGA equipped with 32bit IOMMU only supports 0~4G memory space". [FAQ][rga-faq] Q1.9 and Q4.5: with the multicore driver, an RGA2 job on memory above 4 GiB fails ("RGA2 only support under 4G memory!"); allocate below 4 GiB, as `rga_allocator_dma32_demo.cpp` does. On RK3588, colour fill and planar YUV are RGA2-only. ffmpeg-rockchip sets its DMA32 flag by default ([above](#ffmpeg-rockchips-rga-filters)). |
| **RGA2 and RGA3 scale differently; RGA3 ignores `interp`.** C pins RGA to the RGA3 cores (A1). RGA3's 1/3 kernel is a 3×3 box one pixel off OpenCV's `INTER_AREA` (A4, [preprocess.md](preprocess.md#rga3s-scaler-measured)). | **In part.** [FAQ][rga-faq] Q2.22: on RK3588 the RGA2 and RGA3 cores sample differently when scaling, which shifts the image; it advises choosing the core. The [guide][rga-guide] v1.10.0 on `imresize`: "Only hardware version RGA1/RGA1 plus supports interpolation configuration." The [guide at `2b32edc`][rga-guide-main] (librga 1.10.6) lists RGA3's down-scaling as "average". The kernel's exact offset was not found. |
| **`rknn_inputs_set` leaves outputs bound with `rknn_set_io_mem` unwritten** (A3). | **In general terms.** [User Guide][ug] §5.2.1: "General API and zero-copy API interface cannot be called together." The effect this project saw is not described. |
| **Native NC1HWC2 outputs.** Binding them made `rknn_run` fail ("unsupported tensor dtype … fallback cpu failed"), also with `pass_through`. The project binds NCHW INT8, about 1.2 MB per inference (A6, [spike.md](spike.md)). | **Documented as supported.** [User Guide][ug] Table 5-6 lists NC1HWC2 for int8 models on RK3588, and the model zoo's [YOLOv8 example][mz-zc] binds its native outputs. This project did not run that example. This note does not explain the difference. |
| **Where the NCHW conversion runs** (A6's open question). | **Not settled.** [User Guide][ug] §1.1.3 says the output's layout conversion and dequantisation run "on the CPU or NPU", without saying which for a given binding. |
| **About 1,150 small copies per inference inside `librknnrt.so`,** in B as in C, none longer than 512 bytes (A6, [preprocess.md](preprocess.md#zero-copy-evidence-protocol-114)). | **Not found** in the [User Guide][ug], the [RKNNRT API reference][rt] or rknn-toolkit2's GitHub issues. The guide's §8.1.2 says that with the zero-copy API "the input-output memory is directly accessed by the NPU", which concerns tensor data. |
| **The UINT8 input is converted without the CPU.** A UINT8 binding cost no more CPU per run than an INT8 pass-through binding ([spike.md](spike.md)). | **Yes.** [User Guide][ug] §1.1.3: with the zero-copy API, normalisation and quantisation run on the NPU. |
| **Bound outputs are read through `virt_addr` without `rknn_mem_sync`.** The project sets no init flags and checks at start-up that the CPU reads them coherently ([core.md](core.md)). | **Yes, in the header.** [`rknn_api.h`][rknn-h] describes `RKNN_FLAG_DISABLE_FLUSH_OUTPUT_MEM_CACHE` as "Don't invalid output buffer cache", after which reading `virt_addr` needs `rknn_mem_sync`. |

- **Issue searches:** rknn-toolkit2's GitHub issues were searched for `fallback cpu failed`, `unsupported tensor dtype`, `NC1HWC2`, `memmove`, `memcpy rknn_run` and `rknn_inputs_set rknn_set_io_mem`, and rknn_model_zoo's for `NC1HWC2`. No report of A3's or A6's behaviours, or of the NC1HWC2 binding failure, was found. The hits were about unsupported operators, other chips or model conversion.

## Sources

Rockchip and airockchip:
- [User Guide][ug]: `doc/02_Rockchip_RKNPU_User_Guide_RKNN_SDK_V2.3.2_EN.pdf`, rknn-toolkit2 `v2.3.2`.
- [RKNNRT API reference][rt]: `doc/04_Rockchip_RKNPU_API_Reference_RKNNRT_V2.3.2_EN.pdf`, rknn-toolkit2 `v2.3.2`.
- [`rknn_api.h`][rknn-h], rknn-toolkit2 `v2.3.2`.
- rknn-toolkit2 `v2.3.2` examples: [`rknn_zero_copy` README][tk2-zc], [`rknn_create_mem_demo.cpp`][tk2-mem], [`rknn_create_mem_with_rga_demo.cpp`][tk2-rga].
- rknn_model_zoo `v2.3.2`: [README][mz-readme], [`yolov8_zero_copy.cc`][mz-zc], [`utils/image_utils.c`][mz-img].
- librga: [Developer Guide][rga-guide] and [FAQ][rga-faq] at `v1.10.0`; [Developer Guide at `2b32edc`][rga-guide-main].
- MPP: [Developer Guide][mpp-guide] at `1.1.0`.
- [RK3588S datasheet][ds], Rev 1.5 (2023-11-10), from FriendlyElec's [NanoPi R6C wiki][r6c-wiki].

Others:
- ffmpeg-rockchip at `8b4385e`: [README][ffr-readme], [`vf_vpp_rkrga.c`][ffr-vpp], [`rkrga_common.c`][ffr-common], [`hwcontext_rkmpp.c`][ffr-hwctx].
- GStreamer: [plugin documentation][gst-doc]; Rockchip's plugin at [JeffyCN/mirrors `bff0659`][gst-tree]: [`gstmpp.c`][gst-mpp], [`gstmppdec.c`][gst-dec], [`gstmppvideodec.c`][gst-vdec], [`meson_options.txt`][gst-opt].

[ug]: https://github.com/airockchip/rknn-toolkit2/blob/v2.3.2/doc/02_Rockchip_RKNPU_User_Guide_RKNN_SDK_V2.3.2_EN.pdf
[rt]: https://github.com/airockchip/rknn-toolkit2/blob/v2.3.2/doc/04_Rockchip_RKNPU_API_Reference_RKNNRT_V2.3.2_EN.pdf
[rknn-h]: https://github.com/airockchip/rknn-toolkit2/blob/v2.3.2/rknpu2/runtime/Linux/librknn_api/include/rknn_api.h
[tk2-zc]: https://github.com/airockchip/rknn-toolkit2/blob/v2.3.2/rknpu2/examples/rknn_zero_copy/README.md
[tk2-mem]: https://github.com/airockchip/rknn-toolkit2/blob/v2.3.2/rknpu2/examples/rknn_api_demo/src/rknn_create_mem_demo.cpp
[tk2-rga]: https://github.com/airockchip/rknn-toolkit2/blob/v2.3.2/rknpu2/examples/rknn_api_demo/src/rknn_create_mem_with_rga_demo.cpp
[mz-readme]: https://github.com/airockchip/rknn_model_zoo/blob/v2.3.2/README.md
[mz-zc]: https://github.com/airockchip/rknn_model_zoo/blob/v2.3.2/examples/yolov8/cpp/rknpu2/yolov8_zero_copy.cc
[mz-img]: https://github.com/airockchip/rknn_model_zoo/blob/v2.3.2/utils/image_utils.c
[rga-guide]: https://github.com/airockchip/librga/blob/v1.10.0/docs/Rockchip_Developer_Guide_RGA_EN.md
[rga-faq]: https://github.com/airockchip/librga/blob/v1.10.0/docs/Rockchip_FAQ_RGA_EN.md
[rga-guide-main]: https://github.com/airockchip/librga/blob/2b32edcb97b601b25683e2941d888c8515da6d55/docs/Rockchip_Developer_Guide_RGA_EN.md
[mpp-guide]: https://github.com/rockchip-linux/mpp/blob/1.1.0/doc/Rockchip_Developer_Guide_MPP_EN.md
[ds]: https://wiki.friendlyelec.com/wiki/images/8/8b/Rockchip_RK3588S_Datasheet_V1.5-20231110.pdf
[r6c-wiki]: https://wiki.friendlyelec.com/wiki/index.php/NanoPi_R6C
[ffr-readme]: https://github.com/nyanmisaka/ffmpeg-rockchip/blob/8b4385e985c0a50bf7d3f96cf3cb6d0b63c53e6c/README.md
[ffr-vpp]: https://github.com/nyanmisaka/ffmpeg-rockchip/blob/8b4385e985c0a50bf7d3f96cf3cb6d0b63c53e6c/libavfilter/vf_vpp_rkrga.c
[ffr-common]: https://github.com/nyanmisaka/ffmpeg-rockchip/blob/8b4385e985c0a50bf7d3f96cf3cb6d0b63c53e6c/libavfilter/rkrga_common.c
[ffr-hwctx]: https://github.com/nyanmisaka/ffmpeg-rockchip/blob/8b4385e985c0a50bf7d3f96cf3cb6d0b63c53e6c/libavutil/hwcontext_rkmpp.c
[gst-doc]: https://gstreamer.freedesktop.org/documentation/plugins_doc.html
[gst-tree]: https://github.com/JeffyCN/mirrors/tree/bff0659a51c13585b3c9c9a68aa8c435beeb3fe1
[gst-mpp]: https://github.com/JeffyCN/mirrors/blob/bff0659a51c13585b3c9c9a68aa8c435beeb3fe1/gst/rockchipmpp/gstmpp.c
[gst-dec]: https://github.com/JeffyCN/mirrors/blob/bff0659a51c13585b3c9c9a68aa8c435beeb3fe1/gst/rockchipmpp/gstmppdec.c
[gst-vdec]: https://github.com/JeffyCN/mirrors/blob/bff0659a51c13585b3c9c9a68aa8c435beeb3fe1/gst/rockchipmpp/gstmppvideodec.c
[gst-opt]: https://github.com/JeffyCN/mirrors/blob/bff0659a51c13585b3c9c9a68aa8c435beeb3fe1/meson_options.txt
