// M4's accuracy tool on the board (prereg/M4.md §2.2 to §2.5, §5, §8 step 3). Three modes:
//
//   m4_accuracy pass --model FILE --manifest FILE --inputs DIR --record DIR --out DIR
//                    --run-kind check|m4 --freeze-m4-commit SHA|none [--fixed-clocks FILE]
//                    [--limit N] [--dump N] [--chunk N] [--compare-dump DIR] [--keep-inputs] [--wait-s S]
//     An INT8 pass in the manifest's order, on context 0 (NPU core 0) with M1's bindings, the input
//     bound as B binds its slots (CpuSlots). For each image:
//       - its PNG is decoded (IMREAD_COLOR | IMREAD_IGNORE_ORIENTATION), made RGB in the slot, and the
//         slot's bytes must be the manifest's rgb_sha256; otherwise the pass is invalid (§5) and stops;
//       - on every 100th image (the 1st, the 101st, ...) the bound outputs must equal rknn_outputs_get
//         of a separate context on the same input (§2.5); otherwise the pass is invalid and stops;
//       - the first --dump images' outputs (default 50) are written raw to <record>/dump, for §8 step
//         3's check of the rules, and their SHA-256 to <record>/dump.sha256 (committed; the dumps are
//         not). With --compare-dump DIR (a record's dump) they are also compared byte for byte with that
//         dump (§2.5's determinism), and how many images differ is reported; DIR's files must be the ones
//         its record's dump.sha256 lists before the pass starts;
//       - mAP's rule (§2.3, src/m4/map_rules: yolov8_map or yolov10_map, conf 0.001, at most 300) gives
//         <out>/map/{detections,images}.tsv, as tools/m4_coco.py reads them;
//       - the pipeline's settings (§2.4, M4 §1): YOLOv8n through arm Cp's NeonPost and through
//         yolov8_post, both at conf 0.25 and NMS 0.45, compared field for field (NeonPost's are
//         written); YOLOv10n through yolov10_post at conf 0.25, at most 300. <out>/pipeline/.
//     <record>/pass.csv has the header, the counts, the files' SHA-256, and valid 1, or valid 0 with
//     the reason (§5); any error once the first image has started makes it invalid. <record>/outputs.csv
//     has the outputs' attributes. A pass of run kind m4 runs every image, as root, with --fixed-clocks
//     (tools/set_perf_mode.sh's output), freeze-m4's commit, --compare-dump and --dump of at least 50,
//     deletes its inputs, and needs the compiled objects whose SHA-256 its header records (§2.2, §2.5,
//     §4, §5).
//   m4_accuracy decode --manifest FILE --inputs DIR --record DIR [--limit N] [--chunk N] [--keep-inputs]
//     §8 step 3: every PNG decoded and checked against the manifest's RGB bytes. <record>/decode.csv.
//   m4_accuracy wiring --model FILE --tensors FILE --record DIR [--frames N]
//     §8 step 3: the shared choice of post-processing (PipelinePost, which bench_main and calib_npu
//     call) on the outputs of the first N (at least 10) frames of tensors_c.u8, against this tool's own
//     call with the settings written out below (conf 0.25; NMS 0.45; at most 300): with C10's setting
//     (YOLOv10n, v10) the call of yolov10_post that the pass makes, with C8's (YOLOv8n, neon) NeonPost
//     called directly. Detections must be equal field for field. <record>/wiring.csv, and the
//     detections in <record>/wiring_detections.csv.
//
// Inputs (pass, decode): DIR/chunk_0000, chunk_0001, ... each hold the PNGs <image_id, 12 digits>.png
// of --chunk (default 500) consecutive images of the run. A chunk directory is moved into place whole
// (tools/m4_board_pass.py); the tool waits for it up to --wait-s (default 1800) seconds and deletes it
// once its images have run (§2.2), unless --keep-inputs.
//
// Run from the repository (the header records the compiled objects' SHA-256 by their build paths).
// Exit: pass and decode 0 when valid, 1 when not, 2 when the run cannot start; wiring 0 when equal. A
// model the runtime refuses ends the run in NpuContexts::open with exit 1 and no pass.csv.
#include <unistd.h>

#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <map>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

#include <opencv2/core.hpp>
#include <opencv2/imgcodecs.hpp>
#include <opencv2/imgproc.hpp>

#include "core/metrics.h"
#include "core/npu.h"
#include "core/pipeline_post.h"
#include "core/report.h"
#include "core/util.h"
#include "core/yolov10_post.h"
#include "core/yolov8_post.h"
#include "core/yolov8_post_neon.h"
#include "m4/map_rules.h"
#include "m4/sha256.h"
#include "preprocess/cpu_common.h"

namespace fs = std::filesystem;

namespace {

// Tag freeze-m1 (docs/notes/env.md, Registration), as tools/m4_calib.sh names it.
constexpr const char* kFreezeM1Commit = "2eec964d27545e081a44bc5ead01bbfcd2905a54";
// M1-3's tensor set (A7, M4 §3.1), for the wiring check.
constexpr const char* kTensorsSha256 = "051d6f12662e0b8ef9b72f00f74f498683913c39f197deb42c34ac1a54340c67";
// The compiled mAP rules, relative to the repository (§8 step 3, "Objects").
constexpr const char* kMapRuleObject = "build/CMakeFiles/m4map.dir/src/m4/map_rules.cpp.o";
constexpr int kReferenceEvery = 100;  // §2.5
constexpr long kRulesImages = 50;     // §2.3, §2.5, §8 step 3: the dumped images
constexpr long kWiringFrames = 10;    // §8 step 3
// The pipeline's settings (M4 §1, §2.4), written out here rather than taken from PipelinePost, so that
// the wiring check compares the shared choice with these values and not with its own constants.
constexpr float kPipeConf = 0.25f;
constexpr float kPipeNms = 0.45f;
constexpr int kPipeMaxDet = 300;

[[noreturn]] void refuse(const std::string& why) {
    std::fprintf(stderr, "m4_accuracy: %s\n", why.c_str());
    std::exit(2);
}

std::string read_text(const std::string& path) {
    std::ifstream f(path, std::ios::binary);
    if (!f)
        throw std::runtime_error("cannot read " + path);
    std::stringstream s;
    s << f.rdbuf();
    return s.str();
}

std::string fmt(const char* f, double x) {
    char buf[64];
    std::snprintf(buf, sizeof buf, f, x);
    return buf;
}

std::string image_name(long id) {
    char buf[32];
    std::snprintf(buf, sizeof buf, "%012ld", id);
    return buf;
}

struct Manifest {
    std::vector<long> ids;
    std::vector<std::string> rgb_sha256;
    std::string sha256, rows_sha256;
};

// tools/m4_prep.py's manifest.tsv: "# " header lines, then image_id, ..., rgb_sha256, png_sha256.
Manifest read_manifest(const std::string& path) {
    const std::string text = read_text(path);
    Manifest m;
    m.sha256 = sha256_hex(text);
    std::string rows;  // as m4_prep.rows_sha256: the lines without "# ", newlines included
    std::istringstream in(text);
    std::string line;
    int id_col = -1, sha_col = -1;
    while (std::getline(in, line)) {
        if (line.rfind("# ", 0) == 0)
            continue;
        rows += line + (in.eof() ? "" : "\n");
        std::vector<std::string> cols;
        std::stringstream ls(line);
        for (std::string c; std::getline(ls, c, '\t');)
            cols.push_back(c);
        if (id_col < 0) {
            for (size_t i = 0; i < cols.size(); i++) {
                if (cols[i] == "image_id")
                    id_col = static_cast<int>(i);
                if (cols[i] == "rgb_sha256")
                    sha_col = static_cast<int>(i);
            }
            if (id_col < 0 || sha_col < 0)
                throw std::runtime_error(path + ": no image_id and rgb_sha256 columns");
            continue;
        }
        if (line.empty())
            continue;
        if (static_cast<int>(cols.size()) <= std::max(id_col, sha_col))
            throw std::runtime_error(path + ": a short row");
        m.ids.push_back(std::stol(cols[id_col]));
        m.rgb_sha256.push_back(cols[sha_col]);
    }
    m.rows_sha256 = sha256_hex(rows);
    if (m.ids.empty())
        throw std::runtime_error(path + ": no images");
    return m;
}

using Args = std::map<std::string, std::string>;

Args parse_args(int argc, char** argv, int first, const std::vector<std::string>& flags) {
    Args a;
    for (int i = first; i < argc; i++) {
        std::string k = argv[i];
        if (k.rfind("--", 0) != 0)
            refuse("unexpected argument " + k);
        k = k.substr(2);
        if (std::find(flags.begin(), flags.end(), k) != flags.end()) {
            a[k] = "1";
            continue;
        }
        if (i + 1 >= argc)
            refuse("--" + k + " needs a value");
        a[k] = argv[++i];
    }
    return a;
}

std::string need(const Args& a, const std::string& key) {
    auto it = a.find(key);
    if (it == a.end() || it->second.empty())
        refuse("--" + key + " is needed");
    return it->second;
}

long number(const Args& a, const std::string& key, long dflt, long min) {
    auto it = a.find(key);
    if (it == a.end())
        return dflt;
    char* end = nullptr;
    const long v = std::strtol(it->second.c_str(), &end, 10);
    if (*end || v < min)
        refuse("--" + key + " must be a whole number of at least " + std::to_string(min));
    return v;
}

// The inputs' chunk directories (header comment).
class Inputs {
public:
    Inputs(std::string dir, long chunk, bool keep, long wait_s) : dir_(std::move(dir)), chunk_(chunk), keep_(keep), wait_s_(wait_s) {}

    // The path of the run's i-th image, waiting for its chunk.
    std::string path(long i, long id) {
        const std::string chunk = chunk_dir(i / chunk_);
        const auto until = std::chrono::steady_clock::now() + std::chrono::seconds(wait_s_);
        while (!fs::is_directory(chunk)) {
            if (std::chrono::steady_clock::now() > until)
                throw std::runtime_error(chunk + " did not arrive within " + std::to_string(wait_s_) + " s");
            std::this_thread::sleep_for(std::chrono::milliseconds(500));
        }
        return chunk + "/" + image_name(id) + ".png";
    }

    // After the run's i-th image, of n: deletes its chunk if it was the chunk's last (§2.2).
    void done(long i, long n) {
        if (!keep_ && ((i + 1) % chunk_ == 0 || i + 1 == n))
            fs::remove_all(chunk_dir(i / chunk_));
    }

private:
    std::string chunk_dir(long k) const {
        char buf[32];
        std::snprintf(buf, sizeof buf, "/chunk_%04ld", k);
        return dir_ + buf;
    }
    std::string dir_;
    long chunk_;
    bool keep_;
    long wait_s_;
};

// Decodes a PNG as tools/m4_prep.py wrote it into the slot as RGB and returns its SHA-256.
std::string load_rgb(const std::string& png, uint8_t* slot, int w, int h) {
    cv::Mat bgr = cv::imread(png, cv::IMREAD_COLOR | cv::IMREAD_IGNORE_ORIENTATION);
    if (bgr.empty())
        throw std::runtime_error("cannot decode " + png);
    if (bgr.cols != w || bgr.rows != h)
        throw std::runtime_error(png + " is not " + std::to_string(w) + "x" + std::to_string(h));
    cv::Mat rgb(h, w, CV_8UC3, slot);
    cv::cvtColor(bgr, rgb, cv::COLOR_BGR2RGB);
    if (rgb.data != slot)
        throw std::runtime_error("cvtColor did not write into the slot");
    return sha256_hex(slot, size_t(w) * h * 3);
}

std::string header_text(const Header& h) {
    std::string s;
    for (const auto& [k, v] : h)
        s += "# " + k + ": " + v + "\n";
    return s;
}

FILE* open_table(const std::string& path, const Header& header, const char* columns) {
    FILE* f = std::fopen(path.c_str(), "w");
    if (!f)
        throw std::runtime_error("cannot write " + path);
    std::fputs(header_text(header).c_str(), f);
    std::fputs(columns, f);
    return f;
}

void close_table(FILE* f, const std::string& path) {
    if (std::ferror(f) || std::fclose(f) != 0)
        throw std::runtime_error("writing " + path + " failed");
}

void write_key_values(const std::string& path, const Header& header, const Header& rows, bool echo) {
    FILE* f = open_table(path, header, "key,value\n");
    for (const auto& [k, v] : rows) {
        std::fprintf(f, "%s,%s\n", k.c_str(), v.c_str());
        if (echo)
            std::printf("%s: %s\n", k.c_str(), v.c_str());
    }
    close_table(f, path);
}

// detections.tsv's rows, as tools/m4_coco.py's detection_rows: floats as %.9g.
void write_detections(FILE* f, long id, const std::vector<Detection>& dets) {
    for (size_t n = 0; n < dets.size(); n++) {
        const auto& d = dets[n];
        std::fprintf(f, "%ld\t%zu\t%d\t%.9g\t%.9g\t%.9g\t%.9g\t%.9g\n", id, n, d.cls, d.score, d.x1, d.y1, d.x2, d.y2);
    }
}

bool same(const std::vector<Detection>& a, const std::vector<Detection>& b) {
    if (a.size() != b.size())
        return false;
    for (size_t i = 0; i < a.size(); i++)
        if (a[i].cls != b[i].cls || a[i].score != b[i].score || a[i].x1 != b[i].x1 || a[i].y1 != b[i].y1 ||
            a[i].x2 != b[i].x2 || a[i].y2 != b[i].y2)
            return false;
    return true;
}

std::string model_name(const std::string& sha) {
    if (sha == kYolov8nSha256)
        return "yolov8n";
    if (sha == kYolov10nSha256)
        return "yolov10n";
    return "";
}

// The pass's call of the pipeline's YOLOv10n post-processing (§2.4), which the wiring check compares
// with the shared choice (§8 step 3).
std::vector<Detection> pipeline_v10(const std::vector<const int8_t*>& outputs, const std::vector<rknn_tensor_attr>& attrs,
                                    int mw, int mh) {
    return yolov10_post(outputs, attrs, mw, mh, kPipeConf, kPipeMaxDet);
}

// rknn_outputs_get's outputs for the input from a separate context, as NpuContexts::reference_outputs,
// but throwing on failure, so that the pass records the reason in pass.csv (§5).
std::vector<std::string> reference_outputs(const std::string& model, const NpuContexts& npu, const uint8_t* input,
                                           size_t size) {
    rknn_context ref = 0;
    if (rknn_init(&ref, const_cast<char*>(model.data()), model.size(), NpuContexts::kInitFlags, nullptr) != RKNN_SUCC)
        throw std::runtime_error("the reference context: rknn_init failed");
    rknn_input in{};
    in.index = 0;
    in.type = RKNN_TENSOR_UINT8;
    in.fmt = RKNN_TENSOR_NHWC;
    in.size = static_cast<uint32_t>(size);
    in.buf = const_cast<uint8_t*>(input);
    std::vector<rknn_output> out(npu.outputs.size());
    for (auto& o : out)
        o.want_float = 0;
    const bool ok = rknn_inputs_set(ref, 1, &in) == RKNN_SUCC && rknn_run(ref, nullptr) == RKNN_SUCC &&
                    rknn_outputs_get(ref, static_cast<uint32_t>(out.size()), out.data(), nullptr) == RKNN_SUCC;
    std::vector<std::string> result;
    if (ok) {
        for (auto& o : out)
            result.emplace_back(static_cast<const char*>(o.buf), o.size);
        rknn_outputs_release(ref, static_cast<uint32_t>(out.size()), out.data());
    }
    rknn_destroy(ref);
    if (!ok)
        throw std::runtime_error("the reference context: rknn_inputs_set, rknn_run or rknn_outputs_get failed");
    return result;
}

// "<sha256>  <file>" lines of a dump.sha256, as a map from file to SHA-256.
std::map<std::string, std::string> read_sha256_lines(const std::string& path) {
    std::map<std::string, std::string> m;
    std::istringstream in(read_text(path));
    for (std::string line; std::getline(in, line);)
        if (line.size() > 66 && line[64] == ' ' && line[65] == ' ')
            m[line.substr(66)] = line.substr(0, 64);
    return m;
}

Header common_header(const std::string& kind, const std::string& freeze_m4, const std::string& record) {
    return {
        {"start_time_utc", utc_now("%Y-%m-%dT%H:%M:%SZ")},
        {"run_kind", kind},
        {"prereg_v1_commit", kPreregCommit},
        {"freeze_m1_commit", kFreezeM1Commit},
        {"prereg_m4_commit", kPreregM4Commit},
        {"freeze_m4_commit", freeze_m4},
        {"amendment", LATEST_AMENDMENT},
        {"code_commit", CODE_COMMIT},
        {"record", fs::path(record).filename().string()},
        {"ntp_synchronized", command_output("timedatectl show -p NTPSynchronized --value")},
    };
}

std::string outputs_rows(const std::vector<rknn_tensor_attr>& attrs) {
    std::string s;
    for (size_t k = 0; k < attrs.size(); k++) {
        const auto& a = attrs[k];
        std::string dims;
        for (uint32_t d = 0; d < a.n_dims; d++)
            dims += (d ? "x" : "") + std::to_string(a.dims[d]);
        s += std::to_string(k) + "," + a.name + "," + dims + "," + std::to_string(a.zp) + "," + fmt("%.9g", a.scale) + "\n";
    }
    return s;
}

std::string fixed_clocks(const std::string& path) {
    std::string out;
    std::istringstream in(read_text(path));
    for (std::string line; std::getline(in, line);)
        if (!line.empty())
            out += (out.empty() ? "" : "; ") + line;
    return out;
}

int run_pass(const Args& a) {
    const std::string model = need(a, "model"), record = need(a, "record"), out = need(a, "out");
    const std::string kind = need(a, "run-kind"), freeze_m4 = need(a, "freeze-m4-commit");
    const long limit = number(a, "limit", 0, 1), dump = number(a, "dump", kRulesImages, 0);
    const long chunk = number(a, "chunk", 500, 1), wait_s = number(a, "wait-s", 1800, 1);
    const std::string compare = a.count("compare-dump") ? a.at("compare-dump") : "";
    if (kind != "check" && kind != "m4")
        refuse("--run-kind is check or m4");
    if (kind == "m4" && (!is_commit_sha(freeze_m4) || a.count("limit") || !a.count("fixed-clocks") || geteuid() != 0 ||
                         a.count("keep-inputs") || compare.empty() || dump < kRulesImages))
        refuse("a pass of run kind m4 runs every image as root, with --fixed-clocks, freeze-m4's commit, "
               "--compare-dump (§8 step 3's dump) and --dump of at least 50, and deletes its inputs (M4 §2.2, §2.5, §4)");
    if (kind == "check" && freeze_m4 != "none")
        refuse("run kind check is for before freeze-m4: --freeze-m4-commit none");
    const std::string model_sha = file_sha256(model), name = model_name(model_sha);
    if (name.empty())
        refuse(model + " is neither of M4's models (§1): SHA-256 " + model_sha);
    std::string objects_missing;
    for (const char* o : kPostObjects)
        if (file_sha256(o).empty())
            objects_missing += std::string(" ") + o;
    if (file_sha256(kMapRuleObject).empty())
        objects_missing += std::string(" ") + kMapRuleObject;
    if (kind == "m4" && !objects_missing.empty())
        refuse("run from the repository's build: no" + objects_missing + " (§5, changed objects)");
    Manifest m;
    std::string model_bytes, clocks = "none";
    try {
        m = read_manifest(need(a, "manifest"));
        model_bytes = read_text(model);
        if (a.count("fixed-clocks"))
            clocks = fixed_clocks(a.at("fixed-clocks"));
    } catch (const std::exception& e) {
        refuse(e.what());
    }
    const long n = limit ? std::min<long>(limit, m.ids.size()) : static_cast<long>(m.ids.size());
    if (fs::exists(record + "/pass.csv") || fs::exists(out))
        refuse("the record already has a pass.csv, or " + out + " exists: a pass writes new ones");

    pin_current_thread({4, 5, 6, 7});
    cv::setNumThreads(1);  // PROTOCOL §8
    NpuContexts npu;
    npu.open(model);  // dies (exit 1) if the runtime refuses the model
    const int mw = npu.model_w(), mh = npu.model_h();
    const bool v8 = name == "yolov8n";
    std::unique_ptr<NeonPost> neon;
    std::map<std::string, std::string> compare_sha;
    try {
        if (v8) {
            yolov8_map_check_layout(npu.outputs);
            neon = std::make_unique<NeonPost>(npu.outputs);
        } else {
            yolov10_map_check_layout(npu.outputs);
        }
        // §2.5: the dump compared with must be §8 step 3's whole, as its record's dump.sha256 lists it.
        if (!compare.empty()) {
            compare_sha = read_sha256_lines(compare + "/../dump.sha256");
            for (long i = 0; i < std::min(n, dump); i++)
                for (size_t k = 0; k < npu.outputs.size(); k++) {
                    const std::string file = image_name(m.ids[i]) + "_out" + std::to_string(k) + ".i8";
                    const std::string bytes = read_text(compare + "/" + file);
                    if (!compare_sha.count(file) || sha256_hex(bytes) != compare_sha[file] ||
                        bytes.size() != npu.outputs[k].size)
                        throw std::runtime_error(compare + "/" + file + " is not the one its record's dump.sha256 lists");
                }
        }
        fs::create_directories(record + "/dump");
        fs::create_directories(out + "/map");
        fs::create_directories(out + "/pipeline");
    } catch (const std::exception& e) {
        refuse(e.what());
    }
    CpuSlots slot;
    slot.init(npu, 1);

    const std::string map_rule = v8 ? "yolov8_map: conf 0.001, first 30000, class-wise greedy NMS at IoU 0.7, max 300"
                                    : "yolov10_map: yolov10_post, conf 0.001, max 300, no NMS";
    const std::string pipe_rule = v8 ? "NeonPost (arm Cp), conf 0.25, NMS 0.45; yolov8_post compared field for field"
                                     : "yolov10_post, conf 0.25, max 300, no NMS";
    Header header = common_header(kind, freeze_m4, record);
    const Header pass_fields = {
        {"model", name},
        {"model_file", fs::path(model).filename().string()},
        {"model_sha256", model_sha},
        {"precision", "int8"},
        {"map_rule", map_rule},
        {"pipeline_post", pipe_rule},
        {"manifest", need(a, "manifest")},
        {"manifest_sha256", m.sha256},
        {"manifest_rows_sha256", m.rows_sha256},
        {"images_planned", std::to_string(n)},
        {"sdk_version", npu.sdk_version},
        {"npu", "context 0, NPU core 0; input bound as B binds its slots (CpuSlots), M1's output bindings"},
        {"fixed_clocks", clocks},
        {"user", geteuid() == 0 ? "root" : "not root"},
        {"post_objects_sha256", post_objects_json()},
        {"map_rule_object_sha256", file_sha256(kMapRuleObject)},
    };
    header.insert(header.end(), pass_fields.begin(), pass_fields.end());
    // The two detections files' headers: the pass's, with the pass's name and its rule.
    Header map_header = header, pipe_header = header;
    map_header.push_back({"pass", "int8_" + name});
    map_header.push_back({"rule", map_rule});
    pipe_header.push_back({"pass", "int8_" + name + "_pipeline"});
    pipe_header.push_back({"rule", pipe_rule});

    const std::string map_dets = out + "/map/detections.tsv", map_images = out + "/map/images.tsv";
    const std::string pipe_dets = out + "/pipeline/detections.tsv", pipe_images = out + "/pipeline/images.tsv";
    const char* det_columns = "image_id\trank\tclass\tscore\tx1\ty1\tx2\ty2\n";
    const char* image_columns = "image_id\tcandidates\tdetections\ttied\n";
    FILE *md = nullptr, *mi = nullptr, *pd = nullptr, *pi = nullptr;
    try {
        FILE* f = open_table(record + "/outputs.csv", header, "output,name,dims,zp,scale\n");
        std::fputs(outputs_rows(npu.outputs).c_str(), f);
        close_table(f, record + "/outputs.csv");
        md = open_table(map_dets, map_header, det_columns);
        mi = open_table(map_images, map_header, image_columns);
        pd = open_table(pipe_dets, pipe_header, det_columns);
        pi = open_table(pipe_images, pipe_header, image_columns);
    } catch (const std::exception& e) {
        refuse(e.what());
    }

    Inputs inputs(need(a, "inputs"), chunk, a.count("keep-inputs"), wait_s);
    long done = 0, map_n = 0, pipe_n = 0, tied = 0, references = 0, neon_differs = 0, compared = 0, differ = 0;
    double t_wait = 0, t_decode = 0, t_npu = 0, t_map = 0, t_pipe = 0;
    std::string reason = "none", dump_lines;
    const auto started = std::chrono::steady_clock::now();
    const auto seconds = [](auto t0) { return std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count(); };
    try {
        for (long i = 0; i < n; i++) {
            const long id = m.ids[i];
            auto t0 = std::chrono::steady_clock::now();
            const std::string png = inputs.path(i, id);
            t_wait += seconds(t0);
            t0 = std::chrono::steady_clock::now();
            if (load_rgb(png, slot.data(0), mw, mh) != m.rgb_sha256[i])
                throw std::runtime_error(image_name(id) + ".png's RGB bytes are not the manifest's (M4 §5)");
            t_decode += seconds(t0);
            t0 = std::chrono::steady_clock::now();
            slot.bind(npu, 0, 0);
            if (rknn_run(npu.ctx[0], nullptr) != RKNN_SUCC)
                throw std::runtime_error("rknn_run failed");
            t_npu += seconds(t0);
            const auto ptrs = npu.output_ptrs(0);
            if (i % kReferenceEvery == 0) {
                const auto ref = reference_outputs(model_bytes, npu, slot.data(0), slot.size());
                for (size_t k = 0; k < ref.size(); k++)
                    if (ref[k].size() != npu.outputs[k].size || std::memcmp(ref[k].data(), ptrs[k], ref[k].size()) != 0)
                        throw std::runtime_error("image " + image_name(id) + ": the bound outputs differ from rknn_outputs_get (M4 §5)");
                references++;
            }
            if (i < dump) {
                bool differs = false;
                for (size_t k = 0; k < ptrs.size(); k++) {
                    const std::string file = image_name(id) + "_out" + std::to_string(k) + ".i8";
                    std::ofstream f(record + "/dump/" + file, std::ios::binary);
                    f.write(reinterpret_cast<const char*>(ptrs[k]), npu.outputs[k].size);
                    if (!f)
                        throw std::runtime_error("writing " + record + "/dump/" + file + " failed");
                    const std::string sha = sha256_hex(reinterpret_cast<const uint8_t*>(ptrs[k]), npu.outputs[k].size);
                    dump_lines += sha + "  " + file + "\n";
                    if (!compare.empty())
                        differs |= sha != compare_sha[file];
                }
                if (!compare.empty()) {
                    compared++;
                    differ += differs;
                }
            }
            t0 = std::chrono::steady_clock::now();
            const MapResult r = v8 ? yolov8_map(ptrs, npu.outputs, mw, mh) : yolov10_map(ptrs, npu.outputs, mw, mh);
            t_map += seconds(t0);
            write_detections(md, id, r.dets);
            std::fprintf(mi, "%ld\t%ld\t%zu\t%d\n", id, r.candidates, r.dets.size(), int(r.tied));
            t0 = std::chrono::steady_clock::now();
            std::vector<Detection> p;
            if (v8) {
                p = neon->run(ptrs, npu.outputs, mw, mh, kPipeConf, kPipeNms);
                neon_differs += !same(p, yolov8_post(ptrs, npu.outputs, mw, mh, kPipeConf, kPipeNms));
            } else {
                p = pipeline_v10(ptrs, npu.outputs, mw, mh);
            }
            t_pipe += seconds(t0);
            write_detections(pd, id, p);
            std::fprintf(pi, "%ld\t-1\t%zu\t0\n", id, p.size());
            map_n += static_cast<long>(r.dets.size());
            pipe_n += static_cast<long>(p.size());
            tied += r.tied;
            done++;
            inputs.done(i, n);
            if (done % 100 == 0) {
                std::printf("m4_accuracy: %ld of %ld images, %.0f s\n", done, n, seconds(started));
                std::fflush(stdout);
            }
        }
    } catch (const std::exception& e) {  // §5: an invalid pass, kept and repeated from the start
        reason = e.what();
    }
    const std::pair<FILE*, std::string> tables[] = {{md, map_dets}, {mi, map_images}, {pd, pipe_dets}, {pi, pipe_images}};
    for (const auto& [f, path] : tables) {
        try {
            close_table(f, path);
        } catch (const std::exception& e) {
            if (reason == "none")
                reason = e.what();
        }
    }
    {
        std::ofstream f(record + "/dump.sha256", std::ios::binary);  // §2.5: what later passes compare with
        f << dump_lines;
        if (!f && reason == "none")
            reason = "writing " + record + "/dump.sha256 failed";
    }
    slot.release(npu);
    npu.close();

    const bool valid = reason == "none" && done == n;
    Header rows = {
        {"images", std::to_string(done)},
        {"images_planned", std::to_string(n)},
        {"detections", std::to_string(map_n)},
        {"images_tied", std::to_string(tied)},
        {"pipeline_detections", std::to_string(pipe_n)},
        {"reference_checks", std::to_string(references)},
        {"dumped_images", std::to_string(std::min(done, dump))},
        {"dump_compared_with", compare.empty() ? "none" : compare},
        {"dump_images_compared", std::to_string(compared)},
        {"dump_images_differing", std::to_string(differ)},
    };
    if (v8)
        rows.push_back({"pipeline_neon_vs_yolov8_post_images_differing", std::to_string(neon_differs)});
    const Header after = {
        {"seconds", fmt("%.0f", seconds(started))},
        {"input_wait_s", fmt("%.1f", t_wait)},
        {"decode_s", fmt("%.1f", t_decode)},
        {"npu_s", fmt("%.1f", t_npu)},
        {"map_rule_s", fmt("%.1f", t_map)},
        {"pipeline_post_s", fmt("%.1f", t_pipe)},
        {"out", out},
        {"sha256_map_detections_tsv", file_sha256(map_dets)},
        {"sha256_map_images_tsv", file_sha256(map_images)},
        {"sha256_pipeline_detections_tsv", file_sha256(pipe_dets)},
        {"sha256_pipeline_images_tsv", file_sha256(pipe_images)},
        {"sha256_dump_sha256", file_sha256(record + "/dump.sha256")},
        {"valid", valid ? "1" : "0"},
        {"reason", reason},
    };
    rows.insert(rows.end(), after.begin(), after.end());
    write_key_values(record + "/pass.csv", header, rows, true);
    return valid ? 0 : 1;
}

int run_decode(const Args& a) {
    const std::string record = need(a, "record");
    const long limit = number(a, "limit", 0, 1), chunk = number(a, "chunk", 500, 1), wait_s = number(a, "wait-s", 1800, 1);
    Manifest m;
    try {
        m = read_manifest(need(a, "manifest"));
    } catch (const std::exception& e) {
        refuse(e.what());
    }
    if (fs::exists(record + "/decode.csv"))
        refuse(record + "/decode.csv exists");
    fs::create_directories(record);
    pin_current_thread({4, 5, 6, 7});
    cv::setNumThreads(1);
    Header header = common_header("check", "none", record);
    header.push_back({"manifest", need(a, "manifest")});
    header.push_back({"manifest_sha256", m.sha256});
    header.push_back({"manifest_rows_sha256", m.rows_sha256});
    const long n = limit ? std::min<long>(limit, m.ids.size()) : static_cast<long>(m.ids.size());
    Inputs inputs(need(a, "inputs"), chunk, a.count("keep-inputs"), wait_s);
    std::vector<uint8_t> slot(640 * 640 * 3);
    long done = 0, wrong = 0;
    std::string first = "none", reason = "none";
    const auto started = std::chrono::steady_clock::now();
    try {
        for (long i = 0; i < n; i++) {
            const std::string png = inputs.path(i, m.ids[i]);
            std::string got;
            try {
                got = load_rgb(png, slot.data(), 640, 640);
            } catch (const std::runtime_error& e) {
                got = e.what();
            }
            if (got != m.rgb_sha256[i]) {
                wrong++;
                if (first == "none")
                    first = image_name(m.ids[i]) + ": " + (got.size() == 64 ? "other RGB bytes" : got);
            }
            done++;
            inputs.done(i, n);
        }
    } catch (const std::exception& e) {
        reason = e.what();
    }
    const bool valid = reason == "none" && done == n && wrong == 0;
    const Header rows = {
        {"images", std::to_string(done)},
        {"images_planned", std::to_string(n)},
        {"images_not_the_manifests", std::to_string(wrong)},
        {"first_problem", first},
        {"seconds", fmt("%.0f", std::chrono::duration<double>(std::chrono::steady_clock::now() - started).count())},
        {"valid", valid ? "1" : "0"},
        {"reason", reason},
    };
    write_key_values(record + "/decode.csv", header, rows, true);
    return valid ? 0 : 1;
}

int run_wiring(const Args& a) {
    const std::string model = need(a, "model"), tensors = need(a, "tensors"), record = need(a, "record");
    const long frames = number(a, "frames", kWiringFrames, kWiringFrames);
    const std::string model_sha = file_sha256(model), name = model_name(model_sha);
    if (name.empty())
        refuse(model + " is neither of M4's models (§1)");
    const std::string tensors_sha = file_sha256(tensors);
    if (tensors_sha != kTensorsSha256)
        refuse(tensors + " is not M1-3's tensor set (A7): SHA-256 " + tensors_sha);
    if (fs::exists(record + "/wiring.csv"))
        refuse(record + "/wiring.csv exists");
    std::ifstream in(tensors, std::ios::binary);
    if (!in.good())
        refuse("cannot read " + tensors);
    pin_current_thread({4, 5, 6, 7});
    NpuContexts npu;
    npu.open(model);  // dies (exit 1) if the runtime refuses the model
    const int mw = npu.model_w(), mh = npu.model_h();
    const bool v8 = name == "yolov8n";
    const PostKind kind = v8 ? PostKind::Neon : PostKind::V10;
    std::unique_ptr<PipelinePost> shared;
    std::unique_ptr<NeonPost> neon;
    try {
        shared = std::make_unique<PipelinePost>(kind, npu.outputs);
        if (v8)
            neon = std::make_unique<NeonPost>(npu.outputs);
        fs::create_directories(record);
    } catch (const std::exception& e) {
        refuse(e.what());
    }
    CpuSlots slot;
    slot.init(npu, 1);
    Header header = common_header("check", "none", record);
    header.push_back({"model", name});
    header.push_back({"model_sha256", model_sha});
    header.push_back({"tensors", tensors});
    header.push_back({"tensors_sha256", tensors_sha});
    header.push_back({"setting", v8 ? "C8: PipelinePost neon against NeonPost called directly at conf 0.25, NMS 0.45"
                                    : "C10: PipelinePost v10 against the pass's call of yolov10_post at conf 0.25, max 300"});
    header.push_back({"post_objects_sha256", post_objects_json()});
    header.push_back({"map_rule_object_sha256", file_sha256(kMapRuleObject)});

    const size_t bytes = size_t(mw) * mh * 3;
    const std::string path = record + "/wiring.csv", det_path = record + "/wiring_detections.csv";
    FILE *f = nullptr, *dets = nullptr;
    try {
        f = open_table(path, header, "frame,shared_detections,direct_detections,equal\n");
        dets = open_table(det_path, header, "frame,source,rank,cls,score,x1,y1,x2,y2\n");
    } catch (const std::exception& e) {
        refuse(e.what());
    }
    long equal = 0, total = 0;
    for (long k = 0; k < frames; k++) {
        in.read(reinterpret_cast<char*>(slot.data(0)), static_cast<std::streamsize>(bytes));
        CHECK(in.gcount() == static_cast<std::streamsize>(bytes), "short read in %s", tensors.c_str());
        slot.bind(npu, 0, 0);
        CHECK(rknn_run(npu.ctx[0], nullptr) == RKNN_SUCC, "rknn_run failed");
        const auto ptrs = npu.output_ptrs(0);
        const auto s = shared->run(ptrs, npu.outputs, mw, mh);
        const auto d = v8 ? neon->run(ptrs, npu.outputs, mw, mh, kPipeConf, kPipeNms) : pipeline_v10(ptrs, npu.outputs, mw, mh);
        const bool eq = same(s, d);
        equal += eq;
        total += static_cast<long>(d.size());
        std::fprintf(f, "%ld,%zu,%zu,%d\n", k, s.size(), d.size(), int(eq));
        // §8 step 3 reports the detections: the shared choice's, and the direct call's where they differ.
        for (const auto* list : {&s, &d}) {
            if (list == &d && eq)
                break;
            for (size_t r = 0; r < list->size(); r++) {
                const auto& x = (*list)[r];
                std::fprintf(dets, "%ld,%s,%zu,%d,%.9g,%.9g,%.9g,%.9g,%.9g\n", k, list == &s ? "shared" : "direct", r,
                             x.cls, x.score, x.x1, x.y1, x.x2, x.y2);
            }
        }
    }
    close_table(f, path);
    close_table(dets, det_path);
    slot.release(npu);
    npu.close();
    std::printf("m4_accuracy wiring: %s, %ld of %ld frames equal, %ld detections\n", name.c_str(), equal, frames, total);
    return equal == frames ? 0 : 1;
}

}  // namespace

int main(int argc, char** argv) {
    const std::string mode = argc > 1 ? argv[1] : "";
    try {
        if (mode == "pass")
            return run_pass(parse_args(argc, argv, 2, {"keep-inputs"}));
        if (mode == "decode")
            return run_decode(parse_args(argc, argv, 2, {"keep-inputs"}));
        if (mode == "wiring")
            return run_wiring(parse_args(argc, argv, 2, {}));
    } catch (const std::exception& e) {
        std::fprintf(stderr, "m4_accuracy: %s\n", e.what());
        return 1;
    }
    refuse("usage: m4_accuracy pass|decode|wiring ... (see tools/m4_accuracy.cpp)");
}
