import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import serial_update
import package_firmware
from test_ota_foundation import firmware


class UpdateEngineTests(unittest.TestCase):
    def test_production_state_machine_survives_every_install_write_cut(self):
        compiler = os.environ.get("CC") or shutil.which("gcc")
        self.assertTrue(compiler)
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "update.exe"
            subprocess.run([compiler, "-O2", "-std=c99", "-Wall", "-Wextra", "-Werror",
                            "-I", str(ROOT / "common"), str(ROOT / "tests/update_engine_test.c"),
                            *[str(ROOT / "common" / name) for name in
                              ("ota_image.c", "ota_boot_record.c", "ota_update.c", "ota_protocol.c")],
                            "-o", str(executable)], check=True, capture_output=True)
            subprocess.run([str(executable)], check=True, timeout=60)
            # Drive the actual C protocol with the actual Python sender. Drop a
            # DATA ACK and verify retransmission is idempotent end to end.
            process = subprocess.Popen([str(executable), "serve"], stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, text=True, encoding="ascii")
            class SimulatedSerial:
                response = b""
                dropped = False
                def write(self, wire):
                    fields = serial_update.decode_frame(wire)
                    process.stdin.write(",".join(fields) + "\n")
                    process.stdin.flush()
                    response = process.stdout.readline().strip()
                    self.response = serial_update.frame(response)
                    if fields[4] == "DATA" and not self.dropped:
                        self.dropped = True
                        self.response = b""
                def flush(self):
                    pass
                def readline(self, size):
                    response, self.response = self.response, b""
                    return response
            import struct
            from unittest.mock import patch
            from itertools import count
            port = SimulatedSerial()
            image, manifest = package_firmware.describe_image(firmware(4108), 2)
            try:
                ticks = count(0, 0.1)
                with patch.object(serial_update.time, "monotonic", side_effect=lambda: next(ticks)):
                    serial_update.SerialUpdater(port).update(image, manifest)
                self.assertTrue(port.dropped)
                self.assertEqual(process.wait(timeout=5), 0)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
                process.stdin.close()
                process.stdout.close()

    def test_serial_frames_and_package_validation(self):
        self.assertEqual(serial_update.frame("123456789"), b"@123456789*29B1\n")
        frame = serial_update.frame("OTA,1,1,3,OK,256,2,1")
        self.assertEqual(serial_update.decode_frame(frame)[4], "OK")
        self.assertIsNone(serial_update.decode_frame(frame.replace(b"256", b"258")))
        self.assertIsNone(serial_update.decode_frame(frame[:-1]))
        import struct
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "app.bin"
            output = Path(directory) / "app.zip"
            image.write_bytes(firmware(1023))
            expected = package_firmware.package(image, output, 2)
            data, manifest = serial_update.load_package(output)
            self.assertEqual(manifest, expected)
            self.assertEqual(len(data), 1024)


if __name__ == "__main__":
    unittest.main()
