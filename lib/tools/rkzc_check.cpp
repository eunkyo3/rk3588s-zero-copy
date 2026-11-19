// rkzc-check: can this board run the path? Checks a model and any number of sources, and says why not.
// Usage: rkzc-check [--model FILE.rknn] [--source URL_OR_FILE]...
// Exit status: 0 if every check passed, 1 if one failed, 2 for a usage error.
#include <cstdio>
#include <cstring>
#include <string>

#include "rkzc/rkzc.h"

int main(int argc, char** argv) {
    bool failed = false, any = false;
    for (int i = 1; i < argc; i++) {
        const bool model = std::strcmp(argv[i], "--model") == 0;
        if ((!model && std::strcmp(argv[i], "--source") != 0) || i + 1 >= argc) {
            std::fprintf(stderr, "usage: rkzc-check [--model FILE.rknn] [--source URL_OR_FILE]...\n");
            return 2;
        }
        const std::string arg = argv[++i];
        any = true;
        if (model) {
            rkzc::ModelInfo m;
            const rkzc::Status st = rkzc::check_model(arg, &m);
            if (st)
                std::printf("model  ok      %s: %dx%d, %d outputs, runtime %s\n", arg.c_str(), m.width, m.height,
                            m.outputs, m.runtime.c_str());
            else
                std::printf("model  FAILED  %s: %s\n", arg.c_str(), st.message.c_str());
            failed |= !st.ok;
        } else {
            rkzc::SourceInfo s;
            const rkzc::Status st = rkzc::check_source(arg, &s);
            if (st)
                std::printf("source ok      %s: %dx%d\n", arg.c_str(), s.width, s.height);
            else
                std::printf("source FAILED  %s: %s\n", arg.c_str(), st.message.c_str());
            failed |= !st.ok;
        }
    }
    if (!any)
        std::printf("rkzc %s\n", rkzc::version());
    return failed ? 1 : 0;
}
