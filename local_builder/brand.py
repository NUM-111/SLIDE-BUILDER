"""Brand image helpers; detected colors remain candidates until accepted."""

from __future__ import annotations

import struct
import zlib
from collections import Counter


def image_dimensions(data: bytes, extension: str) -> tuple[int, int] | None:
    if extension.lower() == "png" and data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        return struct.unpack(">II", data[16:24])
    if extension.lower() in ("jpg", "jpeg") and data.startswith(b"\xff\xd8"):
        index = 2
        while index + 9 < len(data):
            if data[index] != 0xFF:
                index += 1
                continue
            marker = data[index + 1]
            index += 2
            if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                continue
            length = int.from_bytes(data[index:index + 2], "big")
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                return int.from_bytes(data[index + 5:index + 7], "big"), int.from_bytes(data[index + 3:index + 5], "big")
            index += length
    return None


def _png_pixels(data: bytes):
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return
    offset = 8
    compressed = bytearray()
    palette = b""
    width = height = color_type = bit_depth = 0
    while offset + 12 <= len(data):
        length = int.from_bytes(data[offset:offset + 4], "big")
        kind = data[offset + 4:offset + 8]
        chunk = data[offset + 8:offset + 8 + length]
        offset += length + 12
        if kind == b"IHDR":
            width, height, bit_depth, color_type = struct.unpack(">IIBB", chunk[:10])
            interlace = chunk[12]
            if interlace or bit_depth != 8 or color_type not in (2, 6):
                return
        elif kind == b"IDAT":
            compressed.extend(chunk)
        elif kind == b"IEND":
            break
    channels = {2: 3, 6: 4}.get(color_type)
    if not channels or not width or width * height > 20_000_000:
        return
    raw = zlib.decompress(bytes(compressed))
    stride = width * channels
    prior = bytearray(stride)
    for row_number in range(height):
        start = row_number * (stride + 1)
        filter_type = raw[start]
        scan = bytearray(raw[start + 1:start + 1 + stride])
        for index in range(stride):
            left = scan[index - channels] if index >= channels else 0
            above = prior[index]
            upper_left = prior[index - channels] if index >= channels else 0
            if filter_type == 1:
                scan[index] = (scan[index] + left) & 255
            elif filter_type == 2:
                scan[index] = (scan[index] + above) & 255
            elif filter_type == 3:
                scan[index] = (scan[index] + ((left + above) // 2)) & 255
            elif filter_type == 4:
                estimate = left + above - upper_left
                distances = (abs(estimate - left), abs(estimate - above), abs(estimate - upper_left))
                predictor = left if distances[0] <= distances[1] and distances[0] <= distances[2] else above if distances[1] <= distances[2] else upper_left
                scan[index] = (scan[index] + predictor) & 255
            elif filter_type != 0:
                return
        for index in range(0, stride, channels):
            pixel = scan[index:index + channels]
            if channels == 3 or pixel[3] > 24:
                yield tuple(pixel[:3])
        prior = scan


def detect_png_colors(data: bytes, limit: int = 5) -> list[dict[str, str | int]]:
    """Return frequent quantized PNG colors for user review, never auto-apply."""
    try:
        pixels = _png_pixels(data)
        if pixels is None:
            return []
        counts = Counter(tuple((channel // 16) * 16 + 8 for channel in pixel) for pixel in pixels)
    except (ValueError, IndexError, struct.error, zlib.error):
        return []
    total = sum(counts.values())
    result = []
    for color, count in counts.most_common(limit * 3):
        if min(color) > 243 or max(color) < 14 or max(color) - min(color) < 18:
            continue
        result.append({"hex": "#%02X%02X%02X" % color, "count": count, "share": round(count / total, 3)})
        if len(result) == limit:
            break
    return result
