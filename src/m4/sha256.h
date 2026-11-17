// SHA-256 (FIPS 180-4) of bytes in memory, for M4's board tool: each input's RGB bytes are checked
// against the manifest (prereg/M4.md §2.2) without writing them to a file for sha256sum.
#pragma once

#include <cstddef>
#include <cstdint>
#include <string>

// The SHA-256 of size bytes at data, as 64 lowercase hex digits.
std::string sha256_hex(const uint8_t* data, size_t size);
std::string sha256_hex(const std::string& s);
