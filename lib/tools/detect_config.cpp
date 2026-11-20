#include "detect_config.h"

#include <cctype>
#include <cerrno>
#include <climits>
#include <cstdlib>
#include <fstream>
#include <set>
#include <sstream>

namespace rkzc_detect {

namespace {

struct Parser {
    const std::string& t;
    size_t p = 0;
    int line = 1;
    std::string error;

    explicit Parser(const std::string& text) : t(text) {}

    bool fail(const std::string& why) {
        if (error.empty())
            error = "line " + std::to_string(line) + ": " + why;
        return false;
    }
    bool end() const { return p >= t.size(); }
    char peek() const { return end() ? '\0' : t[p]; }

    // Spaces, tabs and comments; with newlines too when `lines` is set.
    void skip(bool lines) {
        while (!end()) {
            const char c = t[p];
            if (c == ' ' || c == '\t' || c == '\r') {
                p++;
            } else if (c == '#') {
                while (!end() && t[p] != '\n')
                    p++;
            } else if (c == '\n' && lines) {
                line++;
                p++;
            } else {
                break;
            }
        }
    }

    // After a value or a header: nothing but a comment may follow on the line.
    bool end_of_line() {
        skip(false);
        if (end())
            return true;
        if (t[p] != '\n')
            return fail(std::string("unexpected '") + t[p] + "' after the value");
        return true;
    }

    bool bare_key(std::string* out) {
        const size_t start = p;
        while (!end() && (std::isalnum(static_cast<unsigned char>(t[p])) || t[p] == '_' || t[p] == '-'))
            p++;
        if (p == start)
            return fail("a key was expected");
        *out = t.substr(start, p - start);
        return true;
    }

    bool string(std::string* out) {
        const char quote = t[p++];
        out->clear();
        for (;;) {
            if (end() || t[p] == '\n')
                return fail("a string is not closed");
            const char c = t[p++];
            if (c == quote)
                return true;
            if (c == '\\' && quote == '"') {
                if (end())
                    return fail("a string is not closed");
                const char e = t[p++];
                if (e == '"' || e == '\\')
                    out->push_back(e);
                else if (e == 'n')
                    out->push_back('\n');
                else if (e == 't')
                    out->push_back('\t');
                else
                    return fail(std::string("the escape \\") + e + " is not supported");
            } else {
                out->push_back(c);
            }
        }
    }

    bool value(Value* v) {
        v->line = line;
        const char c = peek();
        if (c == '"' || c == '\'') {
            v->kind = Value::String;
            return string(&v->s);
        }
        if (c == '[') {
            v->kind = Value::StringArray;
            p++;
            for (;;) {
                skip(true);
                if (end())
                    return fail("an array is not closed");
                if (t[p] == ']') {
                    p++;
                    return true;
                }
                if (t[p] != '"' && t[p] != '\'')
                    return fail("an array may hold strings only");
                std::string item;
                if (!string(&item))
                    return false;
                v->a.push_back(item);
                skip(true);
                if (end())
                    return fail("an array is not closed");
                if (peek() == ',')
                    p++;
                else if (peek() != ']')
                    return fail("',' or ']' was expected in the array");
            }
        }
        const size_t start = p;
        while (!end() && !std::isspace(static_cast<unsigned char>(t[p])) && t[p] != '#')
            p++;
        const std::string word = t.substr(start, p - start);
        if (word == "true" || word == "false") {
            v->kind = Value::Bool;
            v->b = word == "true";
            return true;
        }
        if (word.empty())
            return fail("a value was expected");
        size_t digits = (word[0] == '-' || word[0] == '+') ? 1 : 0;
        if (digits == word.size())
            return fail("'" + word + "' is not a value");
        for (size_t i = digits; i < word.size(); i++)
            if (!std::isdigit(static_cast<unsigned char>(word[i])))
                return fail("'" + word + "' is not a string (in quotes), an integer, true or false");
        errno = 0;
        v->kind = Value::Int;
        v->i = std::strtoll(word.c_str(), nullptr, 10);
        if (errno != 0)
            return fail("'" + word + "' is too large");
        return true;
    }
};

}  // namespace

bool parse_toml_subset(const std::string& text, std::map<std::string, Value>* out, std::string* error) {
    Parser ps(text);
    std::string section;
    std::set<std::string> sections;
    out->clear();
    for (;;) {
        ps.skip(true);
        if (ps.end())
            return true;
        bool ok;
        if (ps.peek() == '[') {
            ps.p++;
            ps.skip(false);
            ok = ps.bare_key(&section);
            if (ok) {
                ps.skip(false);
                if (ps.peek() == '.')
                    ok = ps.fail("nested sections are not supported");
                else if (ps.peek() != ']')
                    ok = ps.fail("']' was expected");
                else if (!sections.insert(section).second)
                    ok = ps.fail("the section [" + section + "] is there twice");
                else {
                    ps.p++;
                    ok = ps.end_of_line();
                }
            }
        } else {
            std::string key;
            Value v;
            ok = ps.bare_key(&key);
            if (ok) {
                ps.skip(false);
                if (ps.peek() != '=')
                    ok = ps.fail("'=' was expected after the key " + key);
            }
            if (ok) {
                ps.p++;
                ps.skip(false);
                ok = ps.value(&v) && ps.end_of_line();
            }
            if (ok) {
                const std::string full = section.empty() ? key : section + "." + key;
                if (!out->emplace(full, v).second) {
                    ps.line = v.line;
                    ok = ps.fail("the key " + full + " is there twice");
                }
            }
        }
        if (!ok) {
            *error = ps.error;
            return false;
        }
    }
}

namespace {

const char* kind_name(Value::Kind k) {
    switch (k) {
    case Value::String:
        return "a string";
    case Value::Int:
        return "an integer";
    case Value::Bool:
        return "true or false";
    case Value::StringArray:
        return "an array of strings";
    }
    return "?";
}

// Takes the key out of the table if it is there and has the kind; sets *error otherwise.
const Value* take(std::map<std::string, Value>& table, const std::string& key, Value::Kind kind, Value* slot,
                  std::string* error) {
    const auto it = table.find(key);
    if (it == table.end())
        return nullptr;
    *slot = it->second;
    table.erase(it);
    if (slot->kind != kind) {
        if (error->empty())
            *error = "line " + std::to_string(slot->line) + ": " + key + " must be " + kind_name(kind);
        return nullptr;
    }
    return slot;
}

void take_int(std::map<std::string, Value>& table, const std::string& key, long long min, long long max, int* out,
              std::string* error) {
    Value slot;
    if (const Value* v = take(table, key, Value::Int, &slot, error)) {
        if (v->i < min || v->i > max) {
            if (error->empty())
                *error = "line " + std::to_string(v->line) + ": " + key + " must be between " + std::to_string(min) +
                         " and " + std::to_string(max);
            return;
        }
        *out = static_cast<int>(v->i);
    }
}

void take_bool(std::map<std::string, Value>& table, const std::string& key, bool* out, std::string* error) {
    Value slot;
    if (const Value* v = take(table, key, Value::Bool, &slot, error))
        *out = v->b;
}

}  // namespace

bool parse_detect_config(const std::string& text, DetectConfig* out, std::string* error) {
    std::map<std::string, Value> table;
    error->clear();
    if (!parse_toml_subset(text, &table, error))
        return false;
    DetectConfig c;
    Value slot;
    if (const Value* v = take(table, "model", Value::String, &slot, error))
        c.detector.model = v->s;
    if (const Value* v = take(table, "sources", Value::StringArray, &slot, error))
        c.detector.sources = v->a;
    if (const Value* v = take(table, "post", Value::String, &slot, error))
        c.detector.post = v->s;
    take_int(table, "every_nth_frame", 1, 100000, &c.detector.every_nth_frame, error);
    take_bool(table, "loop_files", &c.detector.loop_files, error);
    take_int(table, "reconnect.initial_ms", 1, 3600000, &c.detector.reconnect_initial_ms, error);
    take_int(table, "reconnect.max_ms", 1, 3600000, &c.detector.reconnect_max_ms, error);
    take_int(table, "reconnect.stall_timeout_ms", 1, 3600000, &c.detector.stall_timeout_ms, error);
    if (const Value* v = take(table, "output.to", Value::String, &slot, error)) {
        c.output = v->s;
        const bool is_unix = c.output.rfind("unix:", 0) == 0;
        if (error->empty() && c.output != "stdout" && !(is_unix && c.output.size() > 5 && c.output[5] == '/'))
            *error = "line " + std::to_string(v->line) +
                     ": output.to must be \"stdout\" or \"unix:/an/absolute/path\"";
        // sockaddr_un holds 108 bytes with the terminator.
        if (error->empty() && is_unix && c.output.size() - 5 > 107)
            *error = "line " + std::to_string(v->line) + ": the socket path is longer than 107 bytes";
    }
    if (const Value* v = take(table, "output.socket_mode", Value::String, &slot, error)) {
        char* rest = nullptr;
        const long mode = std::strtol(v->s.c_str(), &rest, 8);
        if (v->s.empty() || *rest != '\0' || mode < 0 || mode > 0777) {
            if (error->empty())
                *error = "line " + std::to_string(v->line) +
                         ": output.socket_mode must be an octal string such as \"0660\"";
        } else {
            c.socket_mode = static_cast<int>(mode);
        }
    }
    take_bool(table, "output.empty_frames", &c.empty_frames, error);
    take_bool(table, "output.events", &c.events, error);
    take_int(table, "log.stats_every_s", 0, 86400, &c.stats_every_s, error);
    if (!error->empty())
        return false;
    if (!table.empty()) {
        const auto& unknown = *table.begin();
        *error = "line " + std::to_string(unknown.second.line) + ": unknown key " + unknown.first;
        return false;
    }
    if (c.detector.model.empty()) {
        *error = "model is missing";
        return false;
    }
    if (c.detector.sources.empty()) {
        *error = "sources is missing or empty";
        return false;
    }
    *out = c;
    return true;
}

bool load_detect_config(const std::string& path, DetectConfig* out, std::string* error) {
    std::ifstream f(path, std::ios::binary);
    if (!f) {
        *error = "cannot read " + path;
        return false;
    }
    std::ostringstream text;
    text << f.rdbuf();
    if (!parse_detect_config(text.str(), out, error)) {
        *error = path + ": " + *error;
        return false;
    }
    return true;
}

}  // namespace rkzc_detect
