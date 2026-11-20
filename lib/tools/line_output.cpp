#include "line_output.h"

#include <fcntl.h>
#include <grp.h>
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

// Asked of the kernel as each client's send buffer; the kernel caps it at net.core.wmem_max and counts
// its own bookkeeping in it, so a client is dropped after a few hundred unread lines, not after an
// exact number of bytes.
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

// The length of the UTF-8 sequence that starts at s[i], 0 if the bytes there are not valid UTF-8.
size_t utf8_length(const std::string& s, size_t i) {
    const unsigned char c = static_cast<unsigned char>(s[i]);
    size_t n;
    unsigned min;
    unsigned code;
    if (c < 0x80)
        return 1;
    if ((c & 0xE0) == 0xC0) {
        n = 2, min = 0x80, code = c & 0x1F;
    } else if ((c & 0xF0) == 0xE0) {
        n = 3, min = 0x800, code = c & 0x0F;
    } else if ((c & 0xF8) == 0xF0) {
        n = 4, min = 0x10000, code = c & 0x07;
    } else {
        return 0;
    }
    if (i + n > s.size())
        return 0;
    for (size_t k = 1; k < n; k++) {
        const unsigned char t = static_cast<unsigned char>(s[i + k]);
        if ((t & 0xC0) != 0x80)
            return 0;
        code = (code << 6) | (t & 0x3F);
    }
    if (code < min || code > 0x10FFFF || (code >= 0xD800 && code <= 0xDFFF))
        return 0;
    return n;
}

}  // namespace

std::string json_escape(const std::string& s) {
    std::string out;
    out.reserve(s.size() + 2);
    for (size_t i = 0; i < s.size();) {
        const unsigned char c = static_cast<unsigned char>(s[i]);
        if (c >= 0x80) {
            // A path or a message need not be UTF-8; a JSON line must be.
            const size_t n = utf8_length(s, i);
            if (n == 0) {
                out += "\\ufffd";
                i++;
            } else {
                out.append(s, i, n);
                i += n;
            }
            continue;
        }
        if (c == '"' || c == '\\') {
            out.push_back('\\');
            out.push_back(s[i]);
        } else if (c == '\n') {
            out += "\\n";
        } else if (c == '\t') {
            out += "\\t";
        } else if (c == '\r') {
            out += "\\r";
        } else if (c < 0x20 || c == 0x7F) {
            char buf[8];
            std::snprintf(buf, sizeof buf, "\\u%04x", c);
            out += buf;
        } else {
            out.push_back(s[i]);
        }
        i++;
    }
    return out;
}

std::string redact_credentials(const std::string& text) {
    std::string out;
    size_t from = 0;
    for (;;) {
        const size_t scheme = text.find("://", from);
        if (scheme == std::string::npos)
            break;
        const size_t start = scheme + 3;
        // The authority ends at the path, the query, or where the URL itself ends.
        const size_t end = text.find_first_of("/?# \t\r\n\"'<>", start);
        const size_t stop = end == std::string::npos ? text.size() : end;
        const size_t at = text.rfind('@', stop == 0 ? 0 : stop - 1);
        if (at != std::string::npos && at >= start && at < stop) {
            out.append(text, from, start - from);
            out += "***";
            from = at;
        } else {
            out.append(text, from, stop - from);
            from = stop;
        }
    }
    out.append(text, from, std::string::npos);
    return out;
}

LineOutput::~LineOutput() { close(); }

bool LineOutput::open(const std::string& target, const SocketOptions& options, std::string* error) {
    if (target == "stdout")
        return true;
    if (target.rfind("unix:", 0) != 0) {
        *error = "the output must be stdout or unix:/path";
        return false;
    }
    path_ = target.substr(5);
    max_clients_ = options.max_clients;
    sockaddr_un addr;
    if (!fill_address(path_, &addr)) {
        *error = "the socket path is too long: " + path_;
        return false;
    }
    gid_t gid = static_cast<gid_t>(-1);
    if (!options.group.empty()) {
        const group* g = getgrnam(options.group.c_str());
        if (!g) {
            *error = "there is no group " + options.group + " (output.socket_group)";
            return false;
        }
        gid = g->gr_gid;
    }
    struct stat st;
    if (lstat(path_.c_str(), &st) == 0) {
        if (!S_ISSOCK(st.st_mode)) {
            *error = path_ + " exists and is not a socket";
            return false;
        }
        // Does a program still listen there? Only "connection refused" says that none does.
        const int probe = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC | SOCK_NONBLOCK, 0);
        const int rc = probe < 0 ? -1 : connect(probe, reinterpret_cast<const sockaddr*>(&addr), sizeof addr);
        const int why = rc == 0 ? 0 : errno;
        if (probe >= 0)
            ::close(probe);
        if (rc == 0 || why == EAGAIN) {  // EAGAIN: it listens and its queue is full
            *error = path_ + " is in use by another program";
            return false;
        }
        if (why != ECONNREFUSED) {
            *error = path_ + " exists and cannot be examined: " + std::strerror(why);
            return false;
        }
        unlink(path_.c_str());
    }
    listen_fd_ = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC | SOCK_NONBLOCK, 0);
    if (listen_fd_ < 0) {
        *error = errno_text("socket");
        return false;
    }
    if (bind(listen_fd_, reinterpret_cast<const sockaddr*>(&addr), sizeof addr) != 0) {
        *error = errno_text("cannot listen on " + path_);
        ::close(listen_fd_);
        listen_fd_ = -1;
        return false;
    }
    // From here the file is this program's: it is removed again if a later step fails.
    struct stat own;
    const bool have_identity = lstat(path_.c_str(), &own) == 0;
    std::string step;
    if (gid != static_cast<gid_t>(-1) && chown(path_.c_str(), static_cast<uid_t>(-1), gid) != 0)
        step = errno_text("cannot give " + path_ + " to the group " + options.group);
    else if (chmod(path_.c_str(), static_cast<mode_t>(options.mode)) != 0)
        step = errno_text("cannot set the mode of " + path_);
    else if (listen(listen_fd_, 16) != 0 || pipe2(wake_, O_CLOEXEC) != 0)
        step = errno_text("cannot listen on " + path_);
    if (!step.empty()) {
        *error = step;
        ::close(listen_fd_);
        listen_fd_ = -1;
        unlink(path_.c_str());
        return false;
    }
    if (have_identity) {
        dev_ = static_cast<unsigned long long>(own.st_dev);
        ino_ = static_cast<unsigned long long>(own.st_ino);
    }
    is_socket_ = true;
    acceptor_ = std::thread([this] { accept_loop(); });
    return true;
}

void LineOutput::accept_loop() {
    int pause_ms = -1;  // after a failed accept: let the listening socket rest
    for (;;) {
        pollfd fds[2] = {{wake_[0], POLLIN, 0}, {listen_fd_, POLLIN, 0}};
        const int n = poll(fds, pause_ms < 0 ? 2 : 1, pause_ms);
        if (n < 0 && errno != EINTR) {
            std::fprintf(stderr, "rkzc-detect: output: poll failed (%s); no new client is accepted\n",
                         std::strerror(errno));
            return;
        }
        if (n > 0 && fds[0].revents)
            return;
        pause_ms = -1;
        if (n <= 0)
            continue;
        const int fd = accept4(listen_fd_, nullptr, nullptr, SOCK_CLOEXEC | SOCK_NONBLOCK);
        if (fd < 0) {
            if (errno == EMFILE || errno == ENFILE || errno == ENOBUFS || errno == ENOMEM) {
                // The connection stays queued and poll would return at once: wait instead of spinning.
                if (!accept_failed_.exchange(true))
                    std::fprintf(stderr, "rkzc-detect: output: cannot accept a client (%s)\n", std::strerror(errno));
                pause_ms = 200;
            }
            continue;
        }
        accept_failed_ = false;
        std::lock_guard<std::mutex> lk(m_);
        if (static_cast<int>(clients_.size()) >= max_clients_) {
            ::close(fd);
            refused_++;
            continue;
        }
        setsockopt(fd, SOL_SOCKET, SO_SNDBUF, &kSendBuffer, sizeof kSendBuffer);
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
    // Only this program's own file: another program may have replaced it since.
    struct stat st;
    if (lstat(path_.c_str(), &st) == 0 && static_cast<unsigned long long>(st.st_dev) == dev_ &&
        static_cast<unsigned long long>(st.st_ino) == ino_)
        unlink(path_.c_str());
    std::lock_guard<std::mutex> lk(m_);
    for (const int fd : clients_)
        ::close(fd);
    clients_.clear();
}

}  // namespace rkzc_detect
