"""Offline regression cases; no hardware access or controller execution."""

import importlib.util
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "tools" / "analyze_balance_log.py"
SPEC = importlib.util.spec_from_file_location("balance_log", SOURCE)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

HEADER = "DIAG,FW,2026100201,2,15\nDIAG,CFG,2100,960000,5200,620000,1500,170000,2000,600,1300,2600,200\n"


def window(tick, left=2, right=1, enabled=True):
    samples = 100 if enabled else 0
    return (
        f"DIAG,CTRL,{tick},{int(enabled)},{samples},20,-10,1,50,100,-20,30,{left},{right},2,2\n"
        f"DIAG,OBS,{tick},100,{samples},4,3,0,0,2,5,6,100,300\n"
    )


class BalanceLogTests(unittest.TestCase):
    def test_measured_compensation_is_reported_separately_from_legacy_rest(self):
        header = HEADER.replace("2026100201,2,15", "2026100301,2,63")
        comp = "DIAG,COMP,1520,1400,1520,1400,1520,1440,1520,1440,40,40,50,15,60,100,250\n"
        text = header + comp + "".join(window(t) for t in range(500, 31501, 500))
        result = MODULE.analyze(text)
        self.assertEqual(result["parameters"]["deadzone"], 1300)
        self.assertEqual(result["motor_compensation"]["right_forward_run"], 1440)
        self.assertEqual(result["motor_compensation"]["assist_ms"], 60)
        self.assertEqual(result["malformed_records"], 0)

    def test_new_revision_requires_its_measured_compensation_marker(self):
        header = HEADER.replace("2026100201,2,15", "2026100301,2,63")
        result = MODULE.analyze(header + "".join(window(t) for t in range(500, 31501, 500)))
        self.assertEqual(result["status"], "insufficient_continuous_data")

    def test_threshold_change_does_not_join_two_short_observations(self):
        header = HEADER.replace("2026100201,2,15", "2026100301,2,63")
        comp = "DIAG,COMP,1520,1400,1520,1400,1520,1440,1520,1440,40,40,50,15,60,100,250\n"
        text = header + comp + "".join(window(t) for t in range(500, 15501, 500))
        text += comp.replace("1440", "1450") + "".join(window(t) for t in range(16000, 31001, 500))
        self.assertEqual(MODULE.analyze(text)["status"], "insufficient_continuous_data")

    def test_net_travel_is_distinct_from_absolute_travel(self):
        # 31 seconds: analyzer uses the final 30 seconds, i.e. 60 windows.
        text = HEADER + "".join(window(t) for t in range(500, 31501, 500))
        result = MODULE.analyze(text)
        self.assertEqual(result["duration_seconds"], 30)
        self.assertEqual(result["net_encoder_counts_left_right"], [120, 60])
        self.assertEqual(result["absolute_encoder_travel_left_right"], [240, 180])
        self.assertEqual(result["observed_hz"], 200)
        self.assertEqual(result["parameters"]["Mid_Angle"], 2.1)
        self.assertEqual(result["parameters"]["Velocity_Ki"], 15)

    def test_missing_windows_cannot_be_combined_into_a_long_run(self):
        ticks = list(range(500, 15501, 500)) + list(range(17000, 32501, 500))
        result = MODULE.analyze(HEADER + "".join(window(t) for t in ticks))
        self.assertEqual(result["status"], "insufficient_continuous_data")

    def test_motor_disable_breaks_the_stationary_candidate(self):
        text = HEADER + "".join(window(t, enabled=(t != 16000)) for t in range(500, 31501, 500))
        self.assertEqual(MODULE.analyze(text)["status"], "insufficient_continuous_data")

    def test_reboot_cannot_join_two_short_measurements(self):
        text = HEADER + "".join(window(t) for t in range(500, 15501, 500))
        text += HEADER + "".join(window(t) for t in range(500, 15501, 500))
        self.assertEqual(MODULE.analyze(text)["status"], "insufficient_continuous_data")

    def test_unpaired_record_is_not_matched_to_a_different_tick(self):
        text = HEADER + window(500).splitlines()[0] + "\n" + window(1000).splitlines()[1]
        result = MODULE.analyze(text)
        self.assertEqual(result["status"], "insufficient_continuous_data")
        self.assertEqual(result["unmatched_snapshots"], 1)


if __name__ == "__main__":
    unittest.main()
