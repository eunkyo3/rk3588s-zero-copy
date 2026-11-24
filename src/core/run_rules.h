// M4's rules on what a bench_main or calib_npu run may combine (prereg/M4.md §1, §3, §4), in one
// function that both binaries call before they create their output directory, and that test_core
// covers. The other run kinds keep their own checks in the binaries.
#pragma once

#include <string>

#include "core/pipeline_post.h"

struct RunChoice {
    bool calib = false;            // calib_npu; otherwise bench_main
    std::string kind;              // the run kind
    PostKind post = PostKind::M1;  // --post (m1 when not given)
    bool post_runs = true;         // calib_npu: --with-post; bench_main: a variant other than none
    std::string variant;           // bench_main
    std::string freeze_m4 = "none";
    std::string model_sha;         // SHA-256 of the model file
    // The shape of an m4 run.
    int cores = 3;                 // calib_npu: NPU cores
    int channels = 0;              // bench_main
    std::string condition;         // bench_main: main or aux
    bool stagger = false;          // bench_main
    bool a55 = false;              // bench_main: --cores a55
    int prep_threads = 2;          // bench_main: K
};

// The first rule c breaks, as a message for die(); empty when it breaks none.
//   - freeze-m4's commit is none or a full commit SHA.
//   - neon and v10 run in bench_main with variant C only (M2 §1, M4 §3.2), and in calib_npu with
//     run kind check or m4.
//   - v10 runs the YOLOv10n model, and the YOLOv10n model runs with run kind check or m4 only.
//   - An m4 run names freeze-m4's commit and is one of M4's runs:
//     bench_main: arm C8 (variant C, neon, YOLOv8n) or C10 (variant C, v10, YOLOv10n), K = 2 on the
//       A76 cores, at the main point (8 channels, staggered) or the auxiliary one (5 channels), §3.2;
//     calib_npu: one of M4's two models, P_cap on 3 cores with its model's post-processing (neon
//       for YOLOv8n, v10 for YOLOv10n) or the raw limit on 1 core, §3.1.
std::string m4_rule_problem(const RunChoice& c);

// M6's rules (prereg/M6.md §1, §8 step 2), which bench_main calls with what its command line gave.
struct M6Choice {
    std::string kind;                 // the run kind
    std::string variant;              // --variant
    PostKind post = PostKind::M1;     // --post
    std::string cores = "a76";        // --cores: a76, a55 or all
    bool time_rknn = false;           // --time-rknn on
    std::string freeze_m6 = "none";   // --freeze-m6-commit
    // The point, and the options an m6 run leaves at their defaults.
    int channels = 0;
    std::string condition;            // main or aux
    int warmup_s = 0, duration_s = 0;
    bool stagger = false;
    int prep_threads = 2;
    std::string model_path, model_sha;
    bool other_freeze = false;        // a commit given for freeze-m2, freeze-m4 or freeze-m3
};

// The arm of M6 §1 that these options select: B, Bd, Rc, C, By, Cp, Cpf or Ct; empty for anything else.
std::string m6_arm(const std::string& variant, PostKind post, const std::string& cores, bool time_rknn);

// The first rule c breaks, as a message for die(); empty when it breaks none.
//   - freeze-m6's commit is none or a full commit SHA.
//   - Variants Rc and By, --cores all and --time-rknn on are M6's: run kind check or m6.
//   - An m6 run names freeze-m6's commit, is one of the eight arms, and runs at H1's point with the
//     defaults: 8 channels, main condition, 20 s warm-up and a 120 s window, no stagger, K = 2, the
//     YOLOv8n model at its default path, and no commit of freeze-m2, freeze-m4 or freeze-m3.
std::string m6_rule_problem(const M6Choice& c);
