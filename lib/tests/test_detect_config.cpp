// rkzc-detect's configuration reader (lib/tools/detect_config.cpp). It needs no board.
// Usage: test_detect_config [EXAMPLE.toml]     with the file: the shipped example must parse
#include <cstdio>
#include <string>

#include "detect_config.h"

using rkzc_detect::DetectConfig;
using rkzc_detect::parse_detect_config;

static int failures = 0;

static void check(bool ok, const std::string& what) {
    std::printf("%s%s\n", ok ? "ok:   " : "FAIL: ", what.c_str());
    if (!ok)
        failures++;
}

// The text must be refused, with `needle` in the reason.
static void refused(const std::string& text, const std::string& needle, const std::string& what) {
    DetectConfig c;
    std::string error;
    const bool ok = parse_detect_config(text, &c, &error);
    check(!ok && error.find(needle) != std::string::npos, what + ": " + (ok ? "accepted" : error));
}

int main(int argc, char** argv) {
    const std::string base = "model = \"m.rknn\"\nsources = [\"a\"]\n";
    DetectConfig c;
    std::string error;

    check(parse_detect_config(base, &c, &error) && c.detector.model == "m.rknn" && c.detector.sources.size() == 1 &&
              c.detector.every_nth_frame == 6 && c.detector.post == "auto" && !c.detector.loop_files &&
              c.detector.reconnect_max_ms == 30000 && c.output == "stdout" && c.socket_mode == 0666 &&
              c.empty_frames && c.events && c.stats_every_s == 60,
          "the smallest file, with the defaults");

    const std::string full =
        "# a comment\n"
        "model = '/path with spaces/m.rknn'   # after a value\n"
        "sources = [\n"
        "    \"rtsp://h/a#b\",  # a hash inside a string is not a comment\n"
        "    'file \"quoted\".mp4',\n"
        "    \"tab\\there\\\\ \\\"q\\\"\",\n"
        "]\n"
        "every_nth_frame = 3\n"
        "post = \"m1\"\r\n"
        "loop_files = true\n"
        "\n"
        "[output]\n"
        "to = \"unix:/run/rkzc/d.sock\"\n"
        "socket_mode = \"0660\"\n"
        "empty_frames = false\n"
        "events = false\n"
        "[ reconnect ]\n"
        "initial_ms = 100\n"
        "max_ms = 400\n"
        "stall_timeout_ms = 3000\n"
        "[log]\n"
        "stats_every_s = 0";  // no newline at the end
    const bool ok = parse_detect_config(full, &c, &error);
    check(ok, "every key: " + error);
    check(ok && c.detector.model == "/path with spaces/m.rknn" && c.detector.sources.size() == 3 &&
              c.detector.sources[0] == "rtsp://h/a#b" && c.detector.sources[1] == "file \"quoted\".mp4" &&
              c.detector.sources[2] == "tab\there\\ \"q\"",
          "strings: both quotes, the escapes, a hash inside");
    check(ok && c.detector.every_nth_frame == 3 && c.detector.post == "m1" && c.detector.loop_files &&
              c.output == "unix:/run/rkzc/d.sock" && c.socket_mode == 0660 && !c.empty_frames && !c.events &&
              c.detector.reconnect_initial_ms == 100 && c.detector.reconnect_max_ms == 400 &&
              c.detector.stall_timeout_ms == 3000 && c.stats_every_s == 0,
          "every value arrived");

    refused("", "model is missing", "an empty file");
    refused("model = \"m\"\n", "sources is missing", "no sources");
    refused("model = \"m\"\nsources = []\n", "sources is missing", "empty sources");
    refused(base + "every_nth_frmae = 3\n", "line 3: unknown key every_nth_frmae", "a misspelt key");
    refused(base + "[output]\nmodel = \"x\"\n", "unknown key output.model", "a key in the wrong section");
    refused(base + "model = \"again\"\n", "line 3: the key model is there twice", "a key twice");
    refused(base + "[log]\n[log]\n", "line 4: the section [log] is there twice", "a section twice");
    refused(base + "every_nth_frame = \"6\"\n", "line 3: every_nth_frame must be an integer", "a string for an integer");
    refused(base + "every_nth_frame = 0\n", "must be between 1 and", "an integer out of range");
    refused(base + "every_nth_frame = 6.5\n", "line 3", "a float");
    refused(base + "loop_files = yes\n", "line 3", "yes for a boolean");
    refused(base + "post = \"m1\n", "line 3: a string is not closed", "an open string");
    refused("model = \"m\"\nsources = [\"a\", 3]\n", "line 2: an array may hold strings only", "a number in the array");
    refused("model = \"m\"\nsources = [\"a\"\n\n", "an array is not closed", "an open array");
    refused("model = \"m\"\nsources = \"a\"\n", "line 2: sources must be an array of strings", "a string for the array");
    refused(base + "post = \"m1\" extra\n", "line 3: unexpected 'e'", "text after a value");
    refused(base + "post \"m1\"\n", "line 3: '=' was expected", "no equals sign");
    refused(base + "[a.b]\n", "line 3: nested sections", "a nested section");
    refused(base + "[[log]]\n", "line 3", "an array of tables");
    refused(base + "post = \"\\u00e9\"\n", "line 3: the escape", "an unsupported escape");
    refused(base + "[output]\nto = \"tcp://x\"\n", "line 4: output.to must be", "an unknown output");
    refused(base + "[output]\nto = \"unix:relative\"\n", "line 4: output.to must be", "a relative socket path");
    refused(base + "[output]\nto = \"unix:/" + std::string(120, 'x') + "\"\n", "longer than 107", "a long socket path");
    refused(base + "[output]\nsocket_mode = \"rw\"\n", "line 4: output.socket_mode", "a mode that is not octal");
    refused(base + "[output]\nsocket_mode = 660\n", "must be a string", "a mode that is not a string");
    refused(base + "[log]\nstats_every_s = -1\n", "must be between 0 and", "a negative interval");
    refused(base + "every_nth_frame = 99999999999999999999\n", "line 3", "an integer too large");

    if (argc == 2) {
        const bool example = rkzc_detect::load_detect_config(argv[1], &c, &error);
        check(example && c.output.rfind("unix:", 0) == 0 && c.detector.sources.size() == 1,
              std::string("the shipped example parses: ") + argv[1] + " " + error);
        check(!rkzc_detect::load_detect_config("/nonexistent.toml", &c, &error) &&
                  error.find("cannot read") != std::string::npos,
              "a missing file: " + error);
    }
    std::printf(failures ? "%d failed\n" : "all passed\n", failures);
    return failures ? 1 : 0;
}
