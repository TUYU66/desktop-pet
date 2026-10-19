"""按 P3 的四字节帧头逐包读取 Opus，供文件和内存音频共用。"""

import io
import struct


def _decode_stream(stream, callback):
    while True:
        header = stream.read(4)
        if not header:
            return
        if len(header) != 4:
            raise ValueError("Incomplete P3 frame header")
        _, _, size = struct.unpack(">BBH", header)
        packet = stream.read(size)
        if len(packet) != size:
            raise ValueError(f"P3 frame length mismatch: expected {size}, got {len(packet)}")
        callback(packet)


def decode_opus_from_file_stream(input_file, callback):
    with open(input_file, "rb") as stream:
        _decode_stream(stream, callback)


def decode_opus_from_bytes_stream(input_bytes, callback):
    _decode_stream(io.BytesIO(input_bytes), callback)
