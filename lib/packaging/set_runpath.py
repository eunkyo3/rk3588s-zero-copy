#!/usr/bin/env python3
"""Replace the run path (DT_RUNPATH or DT_RPATH) of a 64-bit little-endian ELF file in place.

    set_runpath.py NEW_PATH FILE...

The package bundles ffmpeg-rockchip's libraries, which were linked with the run path of the directory
they were built for (/opt/ffmpeg-rockchip/lib). In the package they live elsewhere and must find each
other there, so their run path becomes $ORIGIN. The new string is written over the old one in the
string table, so it must not be longer; the rest is filled with zero bytes. This does what
`patchelf --set-rpath` does for that case, without needing patchelf on the build host.

Exit status 0 if every file was changed (or already had the path), 1 otherwise.
"""
import struct
import sys

PT_LOAD, PT_DYNAMIC = 1, 2
DT_NULL, DT_STRTAB, DT_RPATH, DT_RUNPATH = 0, 5, 15, 29


def set_runpath(path, new):
    with open(path, "rb") as f:
        data = bytearray(f.read())
    if data[:4] != b"\x7fELF" or data[4] != 2 or data[5] != 1:
        raise ValueError("not a 64-bit little-endian ELF file")
    phoff, = struct.unpack_from("<Q", data, 0x20)
    phentsize, phnum = struct.unpack_from("<HH", data, 0x36)
    loads, dynamic = [], None
    for i in range(phnum):
        p_type, _flags, p_offset, p_vaddr, _paddr, p_filesz = struct.unpack_from("<IIQQQQ", data, phoff + i * phentsize)
        if p_type == PT_LOAD:
            loads.append((p_vaddr, p_offset, p_filesz))
        elif p_type == PT_DYNAMIC:
            dynamic = (p_offset, p_filesz)
    if dynamic is None:
        raise ValueError("no dynamic section")

    def file_offset(vaddr):
        for start, offset, size in loads:
            if start <= vaddr < start + size:
                return offset + (vaddr - start)
        raise ValueError("an address of the dynamic section is in no loaded segment")

    strtab = None
    paths = []
    for off in range(dynamic[0], dynamic[0] + dynamic[1], 16):
        tag, value = struct.unpack_from("<qQ", data, off)
        if tag == DT_NULL:
            break
        if tag == DT_STRTAB:
            strtab = value
        elif tag in (DT_RPATH, DT_RUNPATH):
            paths.append(value)
    if strtab is None or not paths:
        raise ValueError("no run path to replace")
    encoded = new.encode()
    changed = False
    for value in paths:
        at = file_offset(strtab) + value
        end = data.index(b"\0", at)
        old = bytes(data[at:end])
        if old == encoded:
            continue
        if len(encoded) > len(old):
            raise ValueError(f"the new path is longer than the old one ({old.decode()!r})")
        data[at:end] = encoded + b"\0" * (len(old) - len(encoded))
        changed = True
    if changed:
        with open(path, "wb") as f:
            f.write(data)
    return changed


def main():
    if len(sys.argv) < 3:
        print(__doc__, file=sys.stderr)
        return 2
    status = 0
    for path in sys.argv[2:]:
        try:
            print(f"{path}: {'run path set' if set_runpath(path, sys.argv[1]) else 'run path already set'}")
        except (OSError, ValueError, struct.error) as e:
            print(f"{path}: {e}", file=sys.stderr)
            status = 1
    return status


if __name__ == "__main__":
    sys.exit(main())
