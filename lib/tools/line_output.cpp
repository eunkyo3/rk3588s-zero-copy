#include "line_output.h"

#include <fcntl.h>
#include <poll.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>

#include <cerrno>
#include <cstdio>
#include <cstring>

namespace rkzc_detect {

namespace {

// What a client may fall behind before it is dropped. The kernel caps it (net.core.wmem_max).
constexpr int kSendBuffer = 256 * 1024;

std::string errno_text(const std::string& what) { return what + ": " + std::strerror(errno); }

bool fill_address(const std::string& path, sockaddr_un* addr) {
    std::memset(addr, 0, sizeof *addr);
    addr->sun_family = AF_UNIX;
    if (path.size() >= sizeof addr->sun_path)
        return false;
    std::memcpy(addr->sun_path, path.c_str(), path.size());
    return true;
}

}  // namespace

std::string json_escape(const std::string& s) {
    std::string out;
    out.reserve(s.size() + 2);
    for (const char ch : s) {
        const unsigned char c = static_cast<unsigned char>(ch);
        if (c == '"' || c == '\\') {
            out.push_back('\\');
            out.push_back(ch);
        } else if (c == '\n') {
            out += "\\n";
        } else if (c == '\t') {
            out += "\\t";
        } else if (c == '\r') {
            out += "\\r";
        } else if (c < 0x20) {
            char buf[8];
            std::snprintf(buf, sizeof buf, "\\u%04x", c);
            out += buf;
        } else {
            out.push_back(ch);
        }
    }
    return out;
}

LineOutput::~LineOutput() { close(); }

bool LineOutput::open(const std::string& target, int socket_mode, std::string* error) {
    if (target == "stdout")
        return true;
    if (target.rfind("unix:", 0) != 0) {
        *error = "the output must be stdout or unix:/path";
        return false;
    }
    path_ = target.substr(5);
    sockaddr_un addr;
    if (!fill_address(path_, &addr)) {
        *error = "the socket path is too long: " + path_;
        return false;
    }
    struct stat st;
    if (lstat(path_.c_str(), &st) == 0) {
        if (!S_ISSOCK(st.st_mode)) {
            *error = path_ + " exists and is not a socket";
            return false;
        }
        // Does a program still listen there?
        const int probe = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
        const bool answered =
            probe >= 0 && connect(probe, reinterpret_cast<const sockaddr*>(&addr), sizeof addr) == 0;
        if (probe >= 0)
            ::close(probe);
        if (answered) {
            *error = path_ + " is in use by another program";
            return false;
        }
        unlink(path_.c_str());
    }
    listen_fd_ = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC | SOCK_NONBLOCK, 0);
    if (listen_fd_ < 0) {
        *error = errno_text("socket");
        return false;
    }
    if (bind(listen_fd_, reinterpret_cast<const sockaddr*>(&addr), sizeof addr) != 0 ||
        chmod(path_.c_str(), static_cast<mode_t>(socket_mode)) != 0 || listen(listen_fd_, 16) != 0 ||
        pipe2(wake_, O_CLOEXEC) != 0) {
        *error = errno_text("cannot listen on " + path_);
        ::close(listen_fd_);
        listen_fd_ = -1;
        return false;
    }
    is_socket_ = true;
    acceptor_ = std::thread([this] { accept_loop(); });
    return true;
}

void LineOutput::accept_loop() {
    for (;;) {
        pollfd fds[2] = {{listen_fd_, POLLIN, 0}, {wake_[0], POLLIN, 0}};
        if (poll(fds, 2, -1) < 0) {
            if (errno == EINTR)
                continue;
            return;
        }
        if (fds[1].revents)
            return;
        const int fd = accept4(listen_fd_, nullptr, nullptr, SOCK_CLOEXEC | SOCK_NONBLOCK);
        if (fd < 0)
            continue;
        setsockopt(fd, SOL_SOCKET, SO_SNDBUF, &kSendBuffer, sizeof kSendBuffer);
        std::lock_guard<std::mutex> lk(m_);
        clients_.push_back(fd);
    }
}

bool LineOutput::write(const std::string& line) {
    std::string out = line;
    out.push_back('\n');
    if (!is_socket_) {
        // One write per line, so that a reader of a pipe sees whole lines as they come.
        size_t done = 0;
        while (done < out.size()) {
            const ssize_t n = ::write(STDOUT_FILENO, out.data() + done, out.size() - done);
            if (n < 0) {
                if (errno == EINTR)
                    continue;
                return false;
            }
            done += static_cast<size_t>(n);
        }
        return true;
    }
    std::lock_guard<std::mutex> lk(m_);
    for (size_t i = 0; i < clients_.size();) {
        const ssize_t n = send(clients_[i], out.data(), out.size(), MSG_DONTWAIT | MSG_NOSIGNAL);
        if (n == static_cast<ssize_t>(out.size())) {
            i++;
            continue;
        }
        // Gone, or not reading and its buffer is full (possibly after part of this line).
        if (n >= 0 || errno == EAGAIN || errno == EWOULDBLOCK)
            disconnected_++;
        ::close(clients_[i]);
        clients_.erase(clients_.begin() + static_cast<std::ptrdiff_t>(i));
    }
    return true;
}

int LineOutput::clients() {
    std::lock_guard<std::mutex> lk(m_);
    return static_cast<int>(clients_.size());
}

void LineOutput::close() {
    if (!is_socket_)
        return;
    is_socket_ = false;
    const char byte = 0;
    while (::write(wake_[1], &byte, 1) < 0 && errno == EINTR) {
    }
    if (acceptor_.joinable())
        acceptor_.join();
    ::close(listen_fd_);
    ::close(wake_[0]);
    ::close(wake_[1]);
    listen_fd_ = -1;
    unlink(path_.c_str());
    std::lock_guard<std::mutex> lk(m_);
    for (const int fd : clients_)
        ::close(fd);
    clients_.clear();
}

}  // namespace rkzc_detect
