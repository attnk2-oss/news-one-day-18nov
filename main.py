#!/usr/bin/env python3
"""Ship the 18 November 2026 front page as the single PNG the contest expects.

The grader mounts this repository read-only at /bot and hands the entry a
writable /output, so the picture is resolved next to this file and the copy is
written to argv[1], or to /output/entry.png when no path is given.

The picture is checked as a picture before it is copied. The eight signature
bytes must open the file, the IHDR must describe a square canvas inside the
contest's 1024-pixel side and 1 MiB size caps, every chunk's CRC must match
its own contents, and the stream must close on an empty IEND. The copy is made
through a temporary file in the destination directory, then read back and
hashed, so a short or rewritten file can never be reported as shipped.

The standard library is the only dependency, no network is touched, and two
runs of the same program write byte-identical output.
"""

import hashlib
import os
import struct
import sys
import tempfile
import zlib

SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_BYTES = 1048576
MAX_SIDE = 1024
PICTURE = "entry.png"
DEFAULT_OUTPUT = "/output/entry.png"
BLOCK = 1 << 16
HEADER = 33

# The PNG specification fixes which bit depths each colour type may carry.
LEGAL_DEPTHS = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8), 4: (8, 16), 6: (8, 16)}


def read_picture(path):
    """Return the whole file plus the five fields the IHDR carries."""
    with open(path, "rb") as handle:
        blob = handle.read()
    if len(blob) < HEADER or blob[:8] != SIGNATURE:
        raise ValueError("not a PNG: the eight-byte signature is missing")
    width, height, depth, colour, compression = struct.unpack(">IIBBB", blob[16:27])
    if width == 0 or height == 0:
        raise ValueError("the IHDR describes an empty %dx%d canvas" % (width, height))
    if compression != 0:
        raise ValueError("the IHDR names compression method %d; only 0 exists" % compression)
    if colour not in LEGAL_DEPTHS or depth not in LEGAL_DEPTHS[colour]:
        raise ValueError("colour type %d cannot carry a bit depth of %d" % (colour, depth))
    return blob, width, height, depth, colour


def enforce_caps(size, width, height):
    """Refuse anything the contest refuses, naming the cap that broke."""
    if size > MAX_BYTES:
        raise ValueError("the file is %d bytes; the contest allows %d" % (size, MAX_BYTES))
    if width > MAX_SIDE or height > MAX_SIDE:
        raise ValueError("the canvas is %dx%d; the contest allows %d a side"
                         % (width, height, MAX_SIDE))
    if width != height:
        raise ValueError("the canvas is %dx%d; the contest asks for a square" % (width, height))


def scan_chunks(blob):
    """Walk every chunk, checking its length and CRC, and stop at a clean IEND."""
    total = len(blob)
    cursor = 8
    seen = 0
    while True:
        if cursor + 12 > total:
            raise ValueError("the stream ends at byte %d without an IEND" % cursor)
        length = struct.unpack(">I", blob[cursor:cursor + 4])[0]
        kind = blob[cursor + 4:cursor + 8]
        end = cursor + 12 + length
        if end > total:
            raise ValueError("the chunk %r at byte %d runs past the file" % (kind, cursor))
        body = blob[cursor + 8:cursor + 8 + length]
        stored = struct.unpack(">I", blob[cursor + 8 + length:end])[0]
        if zlib.crc32(kind + body) & 0xFFFFFFFF != stored:
            raise ValueError("the chunk %r at byte %d fails its CRC" % (kind, cursor))
        seen += 1
        if kind == b"IEND":
            if length != 0:
                raise ValueError("the IEND carries %d bytes; it must carry none" % length)
            return seen
        cursor = end


def digest_of(path):
    """Hash a file in fixed-size blocks so a big picture never sits in memory twice."""
    running = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            piece = handle.read(BLOCK)
            if not piece:
                break
            running.update(piece)
    return running.hexdigest()


def place_atomically(source, target):
    """Copy source to target without ever exposing a half-written target."""
    folder = os.path.dirname(os.path.abspath(target))
    handle, scratch = tempfile.mkstemp(dir=folder, prefix=".ship-", suffix=".part")
    try:
        with os.fdopen(handle, "wb") as out, open(source, "rb") as src:
            while True:
                piece = src.read(BLOCK)
                if not piece:
                    break
                out.write(piece)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(scratch, 0o644)
        os.replace(scratch, target)
        folder_fd = os.open(folder, os.O_RDONLY)
        try:
            os.fsync(folder_fd)
        finally:
            os.close(folder_fd)
    except BaseException:
        try:
            os.unlink(scratch)
        except OSError:
            pass
        raise


def main(argv):
    target = argv[1] if len(argv) > 1 else DEFAULT_OUTPUT
    source = os.path.join(os.path.dirname(os.path.abspath(__file__)), PICTURE)

    try:
        blob, width, height, depth, colour = read_picture(source)
        enforce_caps(len(blob), width, height)
        chunks = scan_chunks(blob)

        os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
        place_atomically(source, target)

        expected = hashlib.sha256(blob).hexdigest()
        written = digest_of(target)
        if expected != written:
            raise ValueError("the hash changed on the way: %s became %s" % (expected, written))
        grown = os.path.getsize(target)
        if grown != len(blob):
            raise ValueError("the copy is %d bytes; the source is %d" % (grown, len(blob)))
        _, again_w, again_h, again_depth, again_colour = read_picture(target)
        if (again_w, again_h, again_depth, again_colour) != (width, height, depth, colour):
            raise ValueError("the copy's IHDR does not describe the source's canvas")

        print("picture    %s" % source)
        print("bytes      %d of %d allowed" % (len(blob), MAX_BYTES))
        print("canvas     %dx%d, depth %d, colour type %d" % (width, height, depth, colour))
        print("chunks     %d, every CRC good, IEND empty" % chunks)
        print("target     %s" % target)
        print("sha256     %s" % written)
        print("verify     the copy re-reads as the same %dx%d canvas" % (again_w, again_h))
        print("result     a valid PNG, copied byte for byte, inside every cap")
        return 0
    except (OSError, ValueError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
