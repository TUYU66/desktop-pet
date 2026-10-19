import struct
import tempfile
import unittest
from pathlib import Path
from core.utils.p3 import decode_opus_from_bytes_stream, decode_opus_from_file_stream


def frame(packet):
    return struct.pack(">BBH", 0, 0, len(packet)) + packet


class P3StreamingTest(unittest.TestCase):
    def test_file_and_bytes_deliver_identical_ordered_packets(self):
        packets = [b"first", b"second", bytes(range(256))]
        encoded = b"".join(map(frame, packets))
        memory_packets = []
        decode_opus_from_bytes_stream(encoded, memory_packets.append)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.p3"
            path.write_bytes(encoded)
            file_packets = []
            decode_opus_from_file_stream(path, file_packets.append)
        self.assertEqual(packets, memory_packets)
        self.assertEqual(packets, file_packets)

    def test_empty_input_has_no_packets(self):
        packets = []
        decode_opus_from_bytes_stream(b"", packets.append)
        self.assertEqual([], packets)

    def test_truncated_frame_is_not_emitted(self):
        for truncated in [b"\x00", b"\x00\x00\x00", frame(b"broken")[:-1]]:
            with self.subTest(data=truncated):
                packets = []
                with self.assertRaises(ValueError):
                    decode_opus_from_bytes_stream(truncated, packets.append)
                self.assertEqual([], packets)

    def test_callback_failure_propagates(self):
        def fail(_):
            raise RuntimeError("consumer stopped")

        with self.assertRaisesRegex(RuntimeError, "consumer stopped"):
            decode_opus_from_bytes_stream(frame(b"packet"), fail)
