#!/usr/bin/env python3
"""Ship the one-day news front page for make.fun's news-image bounty.

entry.png sits next to this file. The grader mounts the repository read-only
at /bot with a writable /output, so the picture is found from __file__ and the
destination comes from argv[1], defaulting to /output/entry.png.

The check is deliberately strict: eight magic bytes, an IHDR that describes a
square image inside the 1 MiB and 1024-pixel caps, a CRC on every chunk, and
an IEND that closes the stream. The copy goes through a temporary file in the
target directory and is hashed again before the manifest is printed.
"""

import hashlib
import os
import struct
import sys
import tempfile
import zlib

MAGIC = b"\x89PNG\r\n\x1a\n"
CAP_BYTES = 1048576
CAP_SIDE = 1024
PICTURE = "entry.png"
DEFAULT_TARGET = "/output/entry.png"
STEP = 1 << 16


def load_picture(path):
    """Read the file and return (blob, width, height, depth, colour type)."""
    with open(path, "rb") as handle:
        blob = handle.read()
    if len(blob) < 33 or blob[:8] != MAGIC:
        raise ValueError("not a PNG: the 8-byte signature is missing")
    width, height, depth, colour = struct.unpack(">IIBB", blob[16:26])
    if not width or not height:
        raise ValueError("IHDR describes an empty %dx%d canvas" % (width, height))
    return blob, width, height, depth, colour


def enforce_caps(size, width, height):
    """Refuse anything the contest would refuse, and say which cap broke."""
    if size > CAP_BYTES:
        raise ValueError("file is %d bytes; the contest allows %d" % (size, CAP_BYTES))
    if width > CAP_SIDE or height > CAP_SIDE:
        raise ValueError("canvas is %dx%d; the contest allows %d a side"
                         % (width, height, CAP_SIDE))
    if width != height:
        raise ValueError("canvas is %dx%d; the contest wants a square" % (width, height))


def walk_chunks(blob):
    """Count chunks, verifying each length and CRC; stop at a clean IEND."""
    cursor = 8
    count = 0
    total = len(blob)
    while True:
        if cursor + 12 > total:
            raise ValueError("the stream stops at byte %d with no IEND" % cursor)
        length = struct.unpack(">I", blob[cursor:cursor + 4])[0]
        kind = blob[cursor + 4:cursor + 8]
        body = blob[cursor + 8:cursor + 8 + length]
        tail = cursor + 12 + length
        if tail > total:
            raise ValueError("chunk %r at byte %d runs past the file" % (kind, cursor))
        stored = struct.unpack(">I", blob[cursor + 8 + length:tail])[0]
        if zlib.crc32(kind + body) & 0xFFFFFFFF != stored:
            raise ValueError("chunk %r at byte %d has a bad CRC" % (kind, cursor))
        count += 1
        if kind == b"IEND":
            if length != 0:
                raise ValueError("IEND carries %d bytes; it must carry none" % length)
            return count
        cursor = tail


def hash_file(path):
    running = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            piece = handle.read(STEP)
            if not piece:
                break
            running.update(piece)
    return running.hexdigest()


def copy_via_temp(source, target):
    """Write target atomically: temp file beside it, fsync, then replace."""
    folder = os.path.dirname(os.path.abspath(target))
    fd, scratch = tempfile.mkstemp(dir=folder, prefix=".ship-", suffix=".part")
    try:
        with os.fdopen(fd, "wb") as out, open(source, "rb") as src:
            while True:
                piece = src.read(STEP)
                if not piece:
                    break
                out.write(piece)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(scratch, 0o644)
        os.replace(scratch, target)
    except BaseException:
        try:
            os.unlink(scratch)
        except OSError:
            pass
        raise


def main(argv):
    target = argv[1] if len(argv) > 1 else DEFAULT_TARGET
    source = os.path.join(os.path.dirname(os.path.abspath(__file__)), PICTURE)

    try:
        blob, width, height, depth, colour = load_picture(source)
        enforce_caps(len(blob), width, height)
        chunks = walk_chunks(blob)

        os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
        copy_via_temp(source, target)

        before = hashlib.sha256(blob).hexdigest()
        after = hash_file(target)
        if before != after:
            raise ValueError("hash changed in transit: %s -> %s" % (before, after))
        if os.path.getsize(target) != len(blob):
            raise ValueError("copy is %d bytes; the source is %d"
                             % (os.path.getsize(target), len(blob)))

        print("picture    %s" % source)
        print("bytes      %d" % len(blob))
        print("canvas     %dx%d, depth %d, colour %d" % (width, height, depth, colour))
        print("chunks     %d, all CRCs good, IEND clean" % chunks)
        print("target     %s" % target)
        print("sha256     %s" % after)
        print("result     valid PNG, byte-identical copy, inside every cap")
        return 0
    except (OSError, ValueError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
