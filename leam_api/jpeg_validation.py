"""Bounded JPEG stream framing, not pixel decoding or content trust.

A phone image can contain secondary images or metadata after the primary EOI.
Walk segment lengths and entropy escapes so a thumbnail's EOI inside EXIF does
not make a truncated primary image appear complete. Preserve original bytes.
"""


FRAME_MARKERS = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


def jpeg_error(data: bytes) -> str | None:
    if not data.startswith(b"\xff\xd8"):
        return "JPEG start marker is missing"
    pos, size = 2, len(data)
    frame = scanned = entropy = False
    while pos < size:
        if entropy:
            pos = data.find(b"\xff", pos)
            if pos < 0:
                break
        elif data[pos] != 0xFF:
            return "JPEG marker framing is invalid"
        while pos < size and data[pos] == 0xFF:
            pos += 1
        if pos == size:
            break
        marker = data[pos]
        pos += 1
        if entropy and (marker == 0 or 0xD0 <= marker <= 0xD7):
            continue  # Stuffed entropy byte or standalone restart marker.
        if marker == 0xD9:
            if frame and scanned:
                return None  # Remaining bytes are allowed; never rewrite them.
            return "JPEG end marker precedes its image frame or scan"
        if marker == 0x01:
            continue  # Standalone temporary marker, no length field.
        if marker < 0xC0 or marker == 0xD8 or 0xD0 <= marker <= 0xD7:
            return "JPEG marker framing is invalid"
        if pos + 2 > size:
            return "JPEG segment length is incomplete"
        length = int.from_bytes(data[pos:pos + 2], "big")
        if length < 2 or pos + length > size:
            return "JPEG segment is truncated or has an invalid length"
        if marker in FRAME_MARKERS:
            if length < 8 or data[pos + 7] == 0 or length != 8 + 3 * data[pos + 7]:
                return "JPEG image frame header is invalid"
            frame = True
        if marker == 0xDA:
            if not frame or length < 6 or data[pos + 2] == 0 or length != 6 + 2 * data[pos + 2]:
                return "JPEG scan header is invalid"
            scanned = entropy = True
        elif marker != 0xDC:
            entropy = False  # DNL may occur within a scan; tables precede next SOS.
        pos += length
    return "JPEG image data is incomplete; its end-of-image marker is missing"
