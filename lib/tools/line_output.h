// Where rkzc-detect's lines go: stdout, or every client of a Unix stream socket.
#pragma once

#include <atomic>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

namespace rkzc_detect {

struct SocketOptions {
    int mode = 0660;      // the socket file's permission bits; connecting needs the write bit
    std::string group;    // the socket file's group; empty: the process's own
    int max_clients = 16;  // a client beyond this is closed at once
};

class LineOutput {
public:
    LineOutput() = default;
    ~LineOutput();
    LineOutput(const LineOutput&) = delete;
    LineOutput& operator=(const LineOutput&) = delete;

    // target: "stdout" or "unix:/path". For a socket: binds, listens and starts the accepting thread. A
    // socket file that refuses a connection (its program is gone) is replaced; one that answers is an
    // error.
    bool open(const std::string& target, const SocketOptions& options, std::string* error);

    // Writes one line (a newline is added). A socket never waits for a reader: a client whose buffer
    // is full is disconnected, since half a line cannot be taken back. stdout does wait for its reader.
    // False if stdout failed: the reader is gone and the program should end.
    bool write(const std::string& line);

    void close();  // closes the clients and removes the socket file if it is still this program's

    int clients();                                            // connected now (0 for stdout)
    long long disconnected() const { return disconnected_; }  // clients dropped for not reading
    long long refused() const { return refused_; }            // clients closed for being beyond max_clients

private:
    void accept_loop();

    bool is_socket_ = false;
    std::string path_;
    unsigned long long dev_ = 0, ino_ = 0;  // the socket file as this program made it
    int max_clients_ = 16;
    int listen_fd_ = -1;
    int wake_[2] = {-1, -1};
    std::thread acceptor_;
    std::mutex m_;
    std::vector<int> clients_;
    std::atomic<long long> disconnected_{0};
    std::atomic<long long> refused_{0};
    std::atomic<bool> accept_failed_{false};
};

// The string's content for inside JSON quotes. Bytes that are not UTF-8 become U+FFFD.
std::string json_escape(const std::string& s);

// "rtsp://user:password@host/path" with the user and password replaced by "***", wherever such a URL
// stands in the text: what is logged and written to clients must not carry a camera's password.
std::string redact_credentials(const std::string& text);

}  // namespace rkzc_detect
