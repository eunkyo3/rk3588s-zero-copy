#include "core/run_rules.h"

#include "core/report.h"

std::string m4_rule_problem(const RunChoice& c) {
    const bool check_or_m4 = c.kind == "check" || c.kind == "m4";
    const bool v8 = c.model_sha == kYolov8nSha256, v10 = c.model_sha == kYolov10nSha256;
    if (c.freeze_m4 != "none" && !is_commit_sha(c.freeze_m4))
        return "freeze-m4-commit must be none or a full commit SHA";
    if (c.post != PostKind::M1 && !c.calib && c.variant != "C")
        return "post neon and v10 are arms of variant C (M2 §1, M4 §3.2)";
    if (c.post != PostKind::M1 && c.calib && !check_or_m4)
        return "post neon and v10 in calib_npu are M4's: run kind check or m4";
    if (c.post == PostKind::V10 && !v10)
        return "post v10 is for the YOLOv10n model (M4 §1), and this is not it";
    if (v10 && !check_or_m4)
        return "the YOLOv10n model is M4's: run kind check or m4";
    if (c.kind != "m4")
        return "";

    if (!is_commit_sha(c.freeze_m4))
        return "run kind m4 needs freeze-m4's commit (M4 §4)";
    if (c.calib) {
        if (!v8 && !v10)
            return "run kind m4: the model is not one of M4's two (M4 §1)";
        if (c.post_runs && !(c.cores == 3 && c.post == (v10 ? PostKind::V10 : PostKind::Neon)))
            return "run kind m4: P_cap is on 3 cores with its model's post-processing, neon or v10 (M4 §3.1)";
        if (!c.post_runs && c.cores != 1)
            return "run kind m4: the raw limit is on 1 core (M4 §3.1)";
        return "";
    }
    const bool c8 = c.post == PostKind::Neon && v8, c10 = c.post == PostKind::V10 && v10;
    if (c.variant != "C" || !(c8 || c10))
        return "run kind m4 is arm C8 (variant C, neon, YOLOv8n) or C10 (variant C, v10, YOLOv10n) (M4 §1, §3.2)";
    const bool main_point = c.condition == "main" && c.channels == 8 && c.stagger;
    const bool aux_point = c.condition == "aux" && c.channels == 5;
    if (!(main_point || aux_point) || c.a55 || c.prep_threads != 2)
        return "run kind m4 runs at M2's two points: 8 channels main staggered, or 5 channels aux, with K = 2 on "
               "the A76 cores (M4 §3.2)";
    return "";
}
