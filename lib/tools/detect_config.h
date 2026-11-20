// rkzc-detect's configuration file: a subset of TOML, read without a library.
//
// The subset: comments (#), [section] headers of one level, and `key = value` where the value is a
// string ("..." with the escapes \" \\ \n \t, or '...'), an integer, true or false, or an array of
// strings, which may run over several lines. Everything else of TOML is an error with its line number,
// and so is a key this program does not know: a misspelt key must not be ignored silently.
#pragma once

#include <map>
#include <string>
#include <vector>

#include "rkzc/rkzc.h"

namespace rkzc_detect {

struct Value {
    enum Kind { String, Int, Bool, StringArray } kind = String;
    std::string s;
    long long i = 0;
    bool b = false;
    std::vector<std::string> a;
    int line = 0;
};

// Keys are "key" or "section.key". False on a syntax error, with "line N: why" in *error.
bool parse_toml_subset(const std::string& text, std::map<std::string, Value>* out, std::string* error);

struct DetectConfig {
    rkzc::Config detector;
    // "stdout", or "unix:PATH": a Unix stream socket the program listens on; every client gets the lines.
    std::string output = "stdout";
    int socket_mode = 0666;    // the socket file's permission bits
    bool empty_frames = true;  // a line for a frame with no detection too
    bool events = true;        // source events as lines on the output too (they always go to the log)
    int stats_every_s = 60;    // a log line per channel this often; 0: never
};

// The file's keys (see lib/dist/detect.toml for the commented example):
//   model, sources, every_nth_frame, post, loop_files
//   [output]    to, socket_mode, empty_frames, events
//   [reconnect] initial_ms, max_ms, stall_timeout_ms
//   [log]       stats_every_s
bool parse_detect_config(const std::string& text, DetectConfig* out, std::string* error);
bool load_detect_config(const std::string& path, DetectConfig* out, std::string* error);

}  // namespace rkzc_detect
