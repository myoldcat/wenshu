"""Read bounded raster headers for saved-image views, without new dependencies.

Only immutable, already-saved assets are inspected. Dimensions never come from
HTML attributes or image alt text. This does not decode pixels or judge whether
an illustration is semantically suitable; that is a separate selection step.
"""
from __future__ import annotations

import os
from pathlib import Path
import struct
import zlib


MAX_DIMENSION = 100_000
MAX_HEADER_CHUNKS = 1024


def _valid(width, height):
    return (width, height) if 0 < width <= MAX_DIMENSION and 0 < height <= MAX_DIMENSION else (0, 0)


def raster_dimensions(path: Path) -> tuple[int, int]:
    """Return real header dimensions, or (0, 0) for unsupported/broken assets.

    PNG/GIF/WebP need only small fixed headers. JPEG segment payloads are sought
    over, not loaded, so large EXIF profiles cannot inflate list-request memory.
    Header validation is deliberately conservative; browser decode errors still
    need their own no-placeholder handling in the card.
    """
    try:
        with path.open('rb') as stream:
            size = os.fstat(stream.fileno()).st_size
            head = stream.read(33)
            if head.startswith(b'\x89PNG\r\n\x1a\n'):
                if (len(head) < 33 or head[8:16] != b'\0\0\0\rIHDR'
                        or zlib.crc32(head[12:29]) != struct.unpack('>I', head[29:33])[0]):
                    return 0, 0
                return _valid(*struct.unpack('>II', head[16:24]))
            if head[:6] in (b'GIF87a', b'GIF89a') and len(head) >= 13:
                return _valid(*struct.unpack('<HH', head[6:10]))
            if head[:2] == b'\xff\xd8':
                stream.seek(2)
                for _ in range(MAX_HEADER_CHUNKS):
                    marker_start = stream.read(1)
                    if marker_start != b'\xff':
                        return 0, 0
                    marker = stream.read(1)
                    fill_bytes = 0
                    while marker == b'\xff':
                        fill_bytes += 1
                        if fill_bytes > 64:
                            return 0, 0
                        marker = stream.read(1)
                    if not marker or marker[0] in (0x00, 0xD9, 0xDA):
                        return 0, 0
                    if marker[0] == 0x01 or 0xD0 <= marker[0] <= 0xD8:
                        continue
                    length_bytes = stream.read(2)
                    if len(length_bytes) != 2:
                        return 0, 0
                    length = struct.unpack('>H', length_bytes)[0]
                    if length < 2 or stream.tell() + length - 2 > size:
                        return 0, 0
                    if marker[0] in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                                      0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                        frame = stream.read(6)
                        if len(frame) != 6 or length < 8 or length != 8 + 3 * frame[5]:
                            return 0, 0
                        height, width = struct.unpack('>HH', frame[1:5])
                        return _valid(width, height)
                    stream.seek(length - 2, os.SEEK_CUR)
                return 0, 0
            if head[:4] == b'RIFF' and head[8:12] == b'WEBP' and len(head) >= 12:
                riff_end = struct.unpack('<I', head[4:8])[0] + 8
                if riff_end > size or riff_end < 20:
                    return 0, 0
                stream.seek(12)
                for _ in range(MAX_HEADER_CHUNKS):
                    chunk = stream.read(8)
                    if len(chunk) != 8:
                        return 0, 0
                    length = struct.unpack('<I', chunk[4:])[0]
                    end = stream.tell() + length + (length % 2)
                    if end > riff_end:
                        return 0, 0
                    payload = stream.read(min(length, 10))
                    if chunk[:4] == b'VP8X' and len(payload) >= 10:
                        return _valid(int.from_bytes(payload[4:7], 'little') + 1,
                                      int.from_bytes(payload[7:10], 'little') + 1)
                    if chunk[:4] == b'VP8 ' and len(payload) >= 10 and payload[3:6] == b'\x9d\x01\x2a':
                        width, height = struct.unpack('<HH', payload[6:10])
                        return _valid(width & 0x3FFF, height & 0x3FFF)
                    if chunk[:4] == b'VP8L' and len(payload) >= 5 and payload[0] == 0x2F:
                        bits = int.from_bytes(payload[1:5], 'little')
                        return _valid((bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1)
                    stream.seek(end)
                    if end >= riff_end:
                        return 0, 0
    except (OSError, ValueError, struct.error, OverflowError):
        pass
    return 0, 0


def card_image_eligible(width: int, height: int) -> bool:
    """Skip icons, tracking pixels and very narrow decorative strips."""
    shortest, longest = sorted((width, height))
    return shortest >= 160 and longest >= 320 and width * height >= 76_800 and longest <= shortest * 4


def library_image_eligible(width: int, height: int) -> bool:
    """Keep usable article illustrations while excluding small icon-like assets.

    Use real raster dimensions only. Unlike the card-cover shortlist, the full
    image collection keeps square illustrations and long charts without an
    aspect-ratio restriction or a limit on the number of images per article.
    """
    return min(width, height) >= 160 and width * height >= 40_000


def ranked_candidates(assets: list[dict], limit: int = 6) -> list[dict]:
    """Balance usable area with document order; cap the benefit of huge scans.

    Similar-sized images favor an earlier position. A substantially larger later
    illustration can outrank the first image. The result is only a shortlist,
    not an assertion that any candidate is a suitable semantic article cover.
    """
    candidates = [asset for asset in assets if card_image_eligible(asset['width'], asset['height'])]
    candidates.sort(key=lambda asset: (
        -min(asset['width'] * asset['height'], 1_200_000) / (1 + asset['ordinal'] * .25),
        asset['ordinal'], asset['assetId'],
    ))
    return candidates[:limit]
