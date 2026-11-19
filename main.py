#!/usr/bin/env python3
"""Publish the eighteen-november front page as the one file the grader reads.

The grader keeps this repository read-only and hands the program a writable
output directory, so the picture is found beside this script and the copy goes
to argv[1], falling back to /output/entry.png.

A PNG is a header followed by chunks, and this program leans on that shape
instead of on the file extension. It opens with the eight fixed bytes, unpacks
the image header for the canvas size and pixel format, refuses anything past
the contest's one-mebibyte and 1,024-pixel limits, walks the chunk chain while
recomputing each checksum, and insists the chain ends on an empty IEND. Only
then is the picture written, through a scratch file that is renamed into place
so a reader never sees half a file; afterwards the copy is hashed and its
header is read back and compared, and a mismatch is an error rather than a
success message.

Nothing outside the standard library is imported and no socket is opened, so
the same input always produces the same output.
"""

import binascii
import hashlib
import os
import struct
import sys
import tempfile

PNG_OPENING = bytes((137, 80, 78, 71, 13, 10, 26, 10))
LIMIT_BYTES = 1_048_576
LIMIT_SIDE = 1_024
SOURCE_NAME = "entry.png"
FALLBACK = "/output/entry.png"
SLICE = 1 << 16
MIN_HEADER = 33

# Which bit depths the PNG specification allows under each colour type.
ALLOWED_DEPTH = {
    0: frozenset((1, 2, 4, 8, 16)),
    2: frozenset((8, 16)),
    3: frozenset((1, 2, 4, 8)),
    4: frozenset((8, 16)),
    6: frozenset((8, 16)),
}


class Refused(Exception):
    """The file is not something this program will ship."""


def unpack_header(blob):
    """Return (width, height, depth, colour) from the IHDR that must come first."""
    if len(blob) < MIN_HEADER:
        raise Refused("the file is %d bytes, shorter than a PNG header" % len(blob))
    if blob[:8] != PNG_OPENING:
        raise Refused("the eight signature bytes are missing")
    if blob[12:16] != b"IHDR":
        raise Refused("the first chunk is not an IHDR")
    width, height, depth, colour, method, filtering, interlace = struct.unpack(
        ">IIBBBBB", blob[16:29]
    )
    if not width or not height:
        raise Refused("the header gives an empty %dx%d canvas" % (width, height))
    if method != 0 or filtering != 0:
        raise Refused("compression %d / filtering %d is not a PNG method" % (method, filtering))
    if interlace not in (0, 1):
        raise Refused("interlace %d is not defined" % interlace)
    if depth not in ALLOWED_DEPTH.get(colour, ()):
        raise Refused("colour type %d cannot hold %d bits a sample" % (colour, depth))
    return width, height, depth, colour


def within_limits(byte_count, width, height):
    """Compare the picture with the contest's published caps."""
    if byte_count > LIMIT_BYTES:
        raise Refused("%d bytes is over the %d the contest allows" % (byte_count, LIMIT_BYTES))
    if max(width, height) > LIMIT_SIDE:
        raise Refused("%dx%d is larger than %d a side" % (width, height, LIMIT_SIDE))
    if width != height:
        raise Refused("%dx%d is not square" % (width, height))


def walk_chunks(blob):
    """Return the chunk count once the chain reaches an empty IEND."""
    at = 8
    stop = len(blob)
    chunks = 0
    while True:
        if at + 12 > stop:
            raise Refused("the chunk chain breaks at byte %d with no IEND" % at)
        size = struct.unpack(">I", blob[at:at + 4])[0]
        name = blob[at + 4:at + 8]
        after = at + 12 + size
        if after > stop:
            raise Refused("chunk %r at byte %d claims more bytes than remain" % (name, at))
        payload = blob[at + 8:at + 8 + size]
        recorded = struct.unpack(">I", blob[at + 8 + size:after])[0]
        if binascii.crc32(name + payload) & 0xFFFFFFFF != recorded:
            raise Refused("chunk %r at byte %d does not match its checksum" % (name, at))
        chunks += 1
        if name == b"IEND":
            if size:
                raise Refused("the IEND carries %d bytes instead of none" % size)
            return chunks
        at = after


def sha256_of(path):
    """Hash a path in slices, so a large picture is never held twice."""
    state = hashlib.sha256()
    with open(path, "rb") as handle:
        for slice_of_file in iter(lambda: handle.read(SLICE), b""):
            state.update(slice_of_file)
    return state.hexdigest()


def write_through_scratch(source, destination):
    """Copy via a scratch name in the destination folder, then rename over it."""
    folder = os.path.dirname(os.path.abspath(destination))
    descriptor, scratch = tempfile.mkstemp(dir=folder, prefix=".publish-", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "wb") as sink, open(source, "rb") as origin:
            for slice_of_file in iter(lambda: origin.read(SLICE), b""):
                sink.write(slice_of_file)
            sink.flush()
            os.fsync(sink.fileno())
        os.chmod(scratch, 0o644)
        os.replace(scratch, destination)
    except BaseException:
        if os.path.exists(scratch):
            os.unlink(scratch)
        raise


def publish(argv):
    destination = argv[1] if len(argv) > 1 else FALLBACK
    source = os.path.join(os.path.dirname(os.path.abspath(__file__)), SOURCE_NAME)
    try:
        with open(source, "rb") as handle:
            blob = handle.read()
        width, height, depth, colour = unpack_header(blob)
        within_limits(len(blob), width, height)
        chunks = walk_chunks(blob)

        os.makedirs(os.path.dirname(os.path.abspath(destination)), exist_ok=True)
        write_through_scratch(source, destination)

        if sha256_of(source) != sha256_of(destination):
            raise Refused("the bytes changed while being written")
        if os.path.getsize(destination) != len(blob):
            raise Refused("the copy is %d bytes against %d" % (os.path.getsize(destination), len(blob)))
        with open(destination, "rb") as handle:
            back = handle.read(MIN_HEADER)
            handle.seek(-12, os.SEEK_END)
            tail = handle.read(12)
        if unpack_header(back)[:2] != (width, height):
            raise Refused("the copy's header does not match the original")
        if tail[4:8] != b"IEND" or struct.unpack(">I", tail[:4])[0] != 0:
            raise Refused("the copy does not close on an empty IEND")

        print("source   %s" % source)
        print("weight   %d bytes, cap %d" % (len(blob), LIMIT_BYTES))
        print("canvas   %d x %d, %d bits a sample, colour type %d" % (width, height, depth, colour))
        print("chunks   %d, every checksum holds, IEND empty" % chunks)
        print("written  %s" % destination)
        print("sha256   %s" % sha256_of(destination))
        print("verdict  one whole PNG, copied byte for byte, inside every limit")
        return 0
    except (OSError, Refused) as problem:
        print("failed: %s" % problem, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(publish(sys.argv))
