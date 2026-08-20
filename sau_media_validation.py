from __future__ import annotations

import struct
import zlib
from pathlib import Path
from typing import BinaryIO, Callable


SAFE_MEDIA_TYPES = {
    ".gif": "image/gif",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".m4v": "video/x-m4v",
    ".mov": "video/quicktime",
    ".mp4": "video/mp4",
    ".png": "image/png",
    ".webm": "video/webm",
    ".webp": "image/webp",
}

_CHUNK_SIZE = 64 * 1024


def _read_exact(stream: BinaryIO, size: int) -> bytes | None:
    if size < 0 or size > _CHUNK_SIZE:
        return None
    data = stream.read(size)
    return data if len(data) == size else None


def _stream_size(stream: BinaryIO) -> int:
    stream.seek(0, 2)
    size = stream.tell()
    stream.seek(0)
    return size


def _skip_to(stream: BinaryIO, offset: int, file_size: int) -> bool:
    if offset < 0 or offset > file_size:
        return False
    stream.seek(offset)
    return stream.tell() == offset


def _consume_with_crc(
    stream: BinaryIO,
    size: int,
    crc: int,
) -> int | None:
    remaining = size
    while remaining:
        data = stream.read(min(remaining, _CHUNK_SIZE))
        if not data:
            return None
        crc = zlib.crc32(data, crc)
        remaining -= len(data)
    return crc & 0xFFFFFFFF


def _valid_png(stream: BinaryIO, file_size: int) -> bool:
    if file_size < 57 or _read_exact(stream, 8) != b"\x89PNG\r\n\x1a\n":
        return False
    saw_ihdr = False
    saw_idat = False
    while stream.tell() < file_size:
        header = _read_exact(stream, 8)
        if header is None:
            return False
        length = int.from_bytes(header[:4], "big")
        chunk_type = header[4:]
        if not all(
            65 <= byte <= 90 or 97 <= byte <= 122
            for byte in chunk_type
        ):
            return False
        if length > file_size - stream.tell() - 4:
            return False
        if not saw_ihdr:
            if chunk_type != b"IHDR" or length != 13:
                return False
            ihdr = _read_exact(stream, 13)
            if ihdr is None:
                return False
            width, height = struct.unpack(">II", ihdr[:8])
            if (
                width == 0
                or height == 0
                or ihdr[8] not in {1, 2, 4, 8, 16}
                or ihdr[10:12] != b"\x00\x00"
                or ihdr[12] not in {0, 1}
            ):
                return False
            crc = zlib.crc32(ihdr, zlib.crc32(chunk_type)) & 0xFFFFFFFF
            saw_ihdr = True
        else:
            if chunk_type == b"IHDR":
                return False
            crc = _consume_with_crc(stream, length, zlib.crc32(chunk_type))
            if crc is None:
                return False
        expected_crc = _read_exact(stream, 4)
        if expected_crc is None or int.from_bytes(expected_crc, "big") != crc:
            return False
        if chunk_type == b"IDAT":
            saw_idat = True
        elif chunk_type == b"IEND":
            return length == 0 and saw_idat and stream.tell() == file_size
    return False


_JPEG_SOF_MARKERS = frozenset(
    (*range(0xC0, 0xC4), *range(0xC5, 0xC8), *range(0xC9, 0xCC), *range(0xCD, 0xD0))
)


def _valid_jpeg(stream: BinaryIO, file_size: int) -> bool:
    if file_size < 20 or _read_exact(stream, 2) != b"\xff\xd8":
        return False
    saw_sof = False
    while stream.tell() < file_size:
        if _read_exact(stream, 1) != b"\xff":
            return False
        marker_byte = _read_exact(stream, 1)
        while marker_byte == b"\xff":
            marker_byte = _read_exact(stream, 1)
        if marker_byte is None or marker_byte == b"\x00":
            return False
        marker = marker_byte[0]
        if marker == 0xD9:
            return False
        if marker in {*range(0xD0, 0xD8), 0x01}:
            continue
        length_bytes = _read_exact(stream, 2)
        if length_bytes is None:
            return False
        segment_length = int.from_bytes(length_bytes, "big")
        payload_length = segment_length - 2
        payload_start = stream.tell()
        payload_end = payload_start + payload_length
        if payload_length < 0 or payload_end > file_size:
            return False
        if marker in _JPEG_SOF_MARKERS:
            header = _read_exact(stream, min(payload_length, 6))
            if header is None or len(header) < 6:
                return False
            components = header[5]
            if (
                header[0] not in {8, 12, 16}
                or int.from_bytes(header[1:3], "big") == 0
                or int.from_bytes(header[3:5], "big") == 0
                or components == 0
                or segment_length != 8 + 3 * components
            ):
                return False
            saw_sof = True
        elif marker == 0xDA:
            prefix = _read_exact(stream, min(payload_length, 1))
            if prefix is None or not prefix:
                return False
            components = prefix[0]
            if not saw_sof or components == 0 or segment_length != 6 + 2 * components:
                return False
            if not _skip_to(stream, payload_end, file_size):
                return False
            entropy_start = stream.tell()
            if file_size - entropy_start < 3:
                return False
            stream.seek(file_size - 2)
            return _read_exact(stream, 2) == b"\xff\xd9"
        if not _skip_to(stream, payload_end, file_size):
            return False
    return False


def _consume_gif_sub_blocks(stream: BinaryIO, file_size: int) -> bool:
    while stream.tell() < file_size:
        size_byte = _read_exact(stream, 1)
        if size_byte is None:
            return False
        size = size_byte[0]
        if size == 0:
            return True
        if not _skip_to(stream, stream.tell() + size, file_size):
            return False
    return False


def _valid_gif(stream: BinaryIO, file_size: int) -> bool:
    if file_size < 20 or _read_exact(stream, 6) not in {b"GIF87a", b"GIF89a"}:
        return False
    descriptor = _read_exact(stream, 7)
    if descriptor is None:
        return False
    if int.from_bytes(descriptor[:2], "little") == 0 or int.from_bytes(
        descriptor[2:4], "little"
    ) == 0:
        return False
    if descriptor[4] & 0x80:
        table_size = 3 * (2 ** ((descriptor[4] & 0x07) + 1))
        if not _skip_to(stream, stream.tell() + table_size, file_size):
            return False
    saw_image = False
    while stream.tell() < file_size:
        introducer = _read_exact(stream, 1)
        if introducer == b"\x3b":
            return saw_image and stream.tell() == file_size
        if introducer == b"\x21":
            if _read_exact(stream, 1) is None or not _consume_gif_sub_blocks(
                stream, file_size
            ):
                return False
            continue
        if introducer != b"\x2c":
            return False
        image = _read_exact(stream, 9)
        if image is None:
            return False
        if int.from_bytes(image[4:6], "little") == 0 or int.from_bytes(
            image[6:8], "little"
        ) == 0:
            return False
        if image[8] & 0x80:
            table_size = 3 * (2 ** ((image[8] & 0x07) + 1))
            if not _skip_to(stream, stream.tell() + table_size, file_size):
                return False
        code_size = _read_exact(stream, 1)
        if code_size is None or not 2 <= code_size[0] <= 12:
            return False
        if not _consume_gif_sub_blocks(stream, file_size):
            return False
        saw_image = True
    return False


def _valid_webp(stream: BinaryIO, file_size: int) -> bool:
    if file_size < 20 or _read_exact(stream, 4) != b"RIFF":
        return False
    size_bytes = _read_exact(stream, 4)
    if size_bytes is None or int.from_bytes(size_bytes, "little") + 8 != file_size:
        return False
    if _read_exact(stream, 4) != b"WEBP":
        return False
    saw_image = False
    while stream.tell() < file_size:
        header = _read_exact(stream, 8)
        if header is None:
            return False
        chunk_type = header[:4]
        chunk_size = int.from_bytes(header[4:], "little")
        data_start = stream.tell()
        data_end = data_start + chunk_size
        padded_end = data_end + (chunk_size & 1)
        if data_end > file_size or padded_end > file_size:
            return False
        if chunk_type == b"VP8L":
            if chunk_size < 5 or _read_exact(stream, 1) != b"\x2f":
                return False
            saw_image = True
        elif chunk_type == b"VP8 ":
            if chunk_size < 10:
                return False
            prefix = _read_exact(stream, 6)
            if prefix is None or prefix[3:] != b"\x9d\x01\x2a":
                return False
            saw_image = True
        elif chunk_type == b"ANMF":
            if chunk_size < 16:
                return False
            saw_image = True
        elif chunk_type == b"VP8X" and chunk_size != 10:
            return False
        if not _skip_to(stream, padded_end, file_size):
            return False
    return saw_image and stream.tell() == file_size


def _valid_iso_bmff(stream: BinaryIO, file_size: int) -> bool:
    if file_size < 36:
        return False
    offset = 0
    saw_ftyp = False
    saw_mdat = False
    saw_metadata = False
    while offset < file_size:
        if not _skip_to(stream, offset, file_size):
            return False
        header = _read_exact(stream, 8)
        if header is None:
            return False
        box_size = int.from_bytes(header[:4], "big")
        box_type = header[4:]
        header_size = 8
        if box_size == 1:
            extended = _read_exact(stream, 8)
            if extended is None:
                return False
            box_size = int.from_bytes(extended, "big")
            header_size = 16
        elif box_size == 0:
            box_size = file_size - offset
        if box_size < header_size or offset + box_size > file_size:
            return False
        payload_size = box_size - header_size
        if offset == 0 and box_type != b"ftyp":
            return False
        if box_type == b"ftyp":
            if saw_ftyp or payload_size < 8 or payload_size % 4:
                return False
            saw_ftyp = True
        elif box_type == b"mdat":
            if payload_size == 0:
                return False
            saw_mdat = True
        elif box_type in {b"moov", b"moof"}:
            if payload_size < 8:
                return False
            saw_metadata = True
        offset += box_size
    return offset == file_size and saw_ftyp and saw_mdat and saw_metadata


def _read_ebml_vint(
    stream: BinaryIO,
    *,
    keep_marker: bool,
    max_length: int,
) -> int | None:
    first_raw = _read_exact(stream, 1)
    if first_raw is None or first_raw[0] == 0:
        return None
    first = first_raw[0]
    mask = 0x80
    length = 1
    while not first & mask:
        mask >>= 1
        length += 1
    if length > max_length:
        return None
    remainder = _read_exact(stream, length - 1)
    if remainder is None:
        return None
    value = first if keep_marker else first & (mask - 1)
    for byte in remainder:
        value = (value << 8) | byte
    if not keep_marker and value == (1 << (7 * length)) - 1:
        return -1
    return value


def _valid_webm(stream: BinaryIO, file_size: int) -> bool:
    if file_size < 20:
        return False
    if _read_ebml_vint(stream, keep_marker=True, max_length=4) != 0x1A45DFA3:
        return False
    header_size = _read_ebml_vint(stream, keep_marker=False, max_length=8)
    if header_size is None or header_size <= 0 or header_size > 4096:
        return False
    header_end = stream.tell() + header_size
    if header_end > file_size:
        return False
    doc_type = None
    while stream.tell() < header_end:
        element_id = _read_ebml_vint(stream, keep_marker=True, max_length=4)
        element_size = _read_ebml_vint(stream, keep_marker=False, max_length=8)
        if element_id is None or element_size is None or element_size < 0:
            return False
        element_end = stream.tell() + element_size
        if element_end > header_end:
            return False
        if element_id == 0x4282:
            if not 1 <= element_size <= 16:
                return False
            doc_type = _read_exact(stream, element_size)
        if not _skip_to(stream, element_end, file_size):
            return False
    if stream.tell() != header_end or doc_type != b"webm":
        return False
    if _read_ebml_vint(stream, keep_marker=True, max_length=4) != 0x18538067:
        return False
    segment_size = _read_ebml_vint(stream, keep_marker=False, max_length=8)
    if segment_size is None:
        return False
    segment_end = file_size if segment_size == -1 else stream.tell() + segment_size
    if segment_end != file_size:
        return False
    saw_tracks = False
    saw_cluster = False
    while stream.tell() < segment_end:
        element_id = _read_ebml_vint(stream, keep_marker=True, max_length=4)
        element_size = _read_ebml_vint(stream, keep_marker=False, max_length=8)
        if element_id is None or element_size is None:
            return False
        if element_id == 0x1654AE6B:
            saw_tracks = True
        elif element_id == 0x1F43B675:
            saw_cluster = True
        if element_size == -1:
            return element_id == 0x1F43B675 and saw_tracks
        element_end = stream.tell() + element_size
        if element_end > segment_end or not _skip_to(stream, element_end, file_size):
            return False
    return stream.tell() == segment_end and saw_tracks and saw_cluster


_VALIDATORS: dict[str, Callable[[BinaryIO, int], bool]] = {
    ".gif": _valid_gif,
    ".jpeg": _valid_jpeg,
    ".jpg": _valid_jpeg,
    ".m4v": _valid_iso_bmff,
    ".mov": _valid_iso_bmff,
    ".mp4": _valid_iso_bmff,
    ".png": _valid_png,
    ".webm": _valid_webm,
    ".webp": _valid_webp,
}


def detect_media_type(filename: str, stream: BinaryIO) -> str | None:
    suffix = Path(filename).suffix.lower()
    media_type = SAFE_MEDIA_TYPES.get(suffix)
    validator = _VALIDATORS.get(suffix)
    if media_type is None or validator is None:
        return None
    try:
        file_size = _stream_size(stream)
        valid = validator(stream, file_size)
    except (OSError, TypeError, ValueError):
        valid = False
    finally:
        try:
            stream.seek(0)
        except (OSError, ValueError):
            pass
    return media_type if valid else None


__all__ = ["SAFE_MEDIA_TYPES", "detect_media_type"]
