// Where rkzc-detect's lines go: stdout, or every client of a Unix stream socket.
#pragma once

#include <atomic>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

namespace rkzc_detect {

class LineOutput {
public:
    LineOutput() = default;
    ~LineOutput();
    LineOutput(const LineOutput&) = delete;
    LineOutput& operator=(const LineOutput&) = delete;

    // target: "stdout" or "unix:/path". For a socket: binds, listens and starts the accepting thread. A
    // socket file left by a program that is gone is replaced; one that still answers is an error.
    bool open(const std::string& target, int socket_mode, std::string* error);

    // Writes one line (a newline is added). A socket never waits for a reader: a client whose buffer
    // is full is disconnected, since half a line cannot be taken back. stdout does wait for its reader.
    // False if stdout failed: the reader is gone and the program should end.
    bool write(const std::string& line);

    void close();  // closes the clients and removes the socket file

    int clients();                                   // connected now (0 for stdout)
    long long disconnected() const { return disconnected_; }  // clients dropped for not reading

private:
    void accept_loop();

    bool is_socket_ = false;
    std::string path_;
    int listen_fd_ = -1;
    int wake_[2] = {-1, -1};
    std::thread acceptor_;
    std::mutex m_;
    std::vector<int> clients_;
    std::atomic<long long> disconnected_{0};
};

std::string json_escape(const std::string& s);

}  // namespace rkzc_detect
