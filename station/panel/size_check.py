#!/usr/bin/env python3
"""Fails when the panel program does not leave room in its app slot, and prints how full it is, every time.

    python size_check.py <firmware.bin> <partitions.bin>

The slot is the first app partition (factory or ota_0) of the partition table the build produced, so the size is
never written down here. The limit is FK_PANEL_MAX_PERCENT (default 95). Exit 0 under the limit, 1 over it, 2 on bad input.
"""
import os
import struct
import sys

DEFAULT_MAX_PERCENT = 95.0


def app_slot_size(table: bytes) -> int:
    """Size of the first app partition in an ESP-IDF binary partition table (32-byte entries, magic 0x50AA)."""
    for i in range(0, len(table) - 31, 32):
        magic, ptype, subtype, _offset, size = struct.unpack_from("<HBBII", table, i)
        if magic != 0x50AA:
            break  # the MD5 entry (0xEBEB) or the 0xFF padding ends the entries
        if ptype == 0 and (subtype == 0x00 or subtype == 0x10):  # app: factory or ota_0
            return size
    raise ValueError("no app partition in the partition table")


def main(argv) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        limit = float(os.environ.get("FK_PANEL_MAX_PERCENT", DEFAULT_MAX_PERCENT))
        with open(argv[2], "rb") as f:
            slot = app_slot_size(f.read())
        used = os.path.getsize(argv[1])
    except (OSError, ValueError) as e:
        print("size check: %s" % e, file=sys.stderr)
        return 2
    percent = 100.0 * used / slot
    line = "panel program: %d of %d bytes in the app slot (%.1f%%, limit %.1f%%)" % (used, slot, percent, limit)
    if percent > limit:
        print("FAIL: %s" % line, file=sys.stderr)
        return 1
    print("ok: %s" % line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
