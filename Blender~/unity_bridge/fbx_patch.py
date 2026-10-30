"""In-place edits of fixed-size values in binary FBX files."""

import struct

from . import util


def _record(name):
    """Binary FBX 7.x P record head: S name, S "double", S "Number", S "", then D + 8 bytes."""
    def s(text):
        b = text.encode()
        return b"S" + struct.pack("<I", len(b)) + b
    return s(name) + s("double") + s("Number") + s("") + b"D"


def set_unit_scale(path, value):
    """Sets UnitScaleFactor and OriginalUnitScaleFactor. Returns how many values were written."""
    with open(path, "rb") as f:
        data = bytearray(f.read())
    count = 0
    for name in ("UnitScaleFactor", "OriginalUnitScaleFactor"):
        head = _record(name)
        i = data.find(head)
        if i < 0:
            continue
        at = i + len(head)
        data[at:at + 8] = struct.pack("<d", float(value))
        count += 1
    if count:
        with open(path, "wb") as f:
            f.write(data)
    else:
        util.log("UnitScaleFactor not found in", path, "(ASCII FBX?)")
    return count
