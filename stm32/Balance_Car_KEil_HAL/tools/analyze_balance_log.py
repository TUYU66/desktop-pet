"""Read-only analysis of captured ESP32 DIAG lines; never connects to hardware."""

import argparse
import json
import re
from pathlib import Path


ARITY = {"FW": 3, "CFG": 11, "COMP": 15, "CTRL": 14, "OBS": 13}
LINE = re.compile(r"DIAG,(FW|CFG|COMP|CTRL|OBS),(-?\d+(?:,-?\d+)*)(?=\s|\*|\x1b|$)")


def parse_windows(text):
    """Pair atomic CTRL/OBS snapshots. Break runs at resets, gaps or motion."""
    config = firmware = compensation = pending = None
    previous_tick = None
    run = []
    runs = []
    malformed = unmatched = 0

    def finish():
        nonlocal run
        if run:
            runs.append(run)
            run = []

    for line in text.splitlines():
        match = LINE.search(line)
        if not match:
            continue
        kind, csv = match.groups()
        fields = tuple(map(int, csv.split(",")))
        if len(fields) != ARITY[kind] or any(n < -(2**31) or n >= 2**32 for n in fields):
            malformed += 1
            finish()
            pending = None
            continue
        if kind in ("FW", "CFG", "COMP"):
            old = firmware if kind == "FW" else config if kind == "CFG" else compensation
            if old is not None and old != fields:
                finish()
                pending = None
            if kind == "FW":
                if old != fields:
                    compensation = None
                firmware = fields
            elif kind == "CFG":
                config = fields
            else:
                if not all(n > 0 for n in fields) or any(fields[i + 1] > fields[i] for i in range(0, 8, 2)):
                    malformed += 1
                    compensation = None
                    finish()
                    pending = None
                    continue
                compensation = fields
            continue
        if kind == "CTRL":
            if pending is not None:
                unmatched += 1
                finish()
            pending = fields
            continue
        control = pending
        pending = None
        if control is None or control[0] != fields[0]:
            unmatched += 1
            finish()
            continue
        tick = fields[0]
        elapsed = ((tick - previous_tick) & 0xFFFFFFFF) if previous_tick is not None else None
        previous_tick = tick
        # CTRL: enabled samples only; OBS: all ISR samples. Equality excludes
        # transitions and explicit motion. It does not certify physical settling.
        valid = control[1] == 1 and control[2] > 0 and fields[1] == fields[2] == control[2]
        valid = valid and all(n >= 0 for n in fields[:10])
        valid = valid and fields[5] <= fields[1] and fields[6] <= fields[1]
        valid = valid and fields[7] <= fields[1] and fields[8] <= fields[9]
        valid = valid and fields[11] <= fields[12] and control[8] <= control[9]
        valid = valid and control[12] >= 0 and control[13] >= 0
        valid = valid and fields[3] >= abs(control[10]) and fields[4] >= abs(control[11])
        valid = valid and firmware is not None and firmware[1] == 2 and firmware[2] & 8
        valid = valid and config is not None and config[9] > 0 and config[10] > 0
        if firmware is not None and firmware[2] & 32:
            if compensation is None or config is None:
                valid = False
            else:
                valid = valid and all(n <= config[9] for n in compensation[:8])
        if not valid:
            finish()
            continue
        # At 2 Hz, allow scheduling jitter but exclude lost windows and resets.
        if elapsed is None or not 250 <= elapsed <= 1000:
            finish()
            continue
        run.append((elapsed, control, fields, config, firmware, compensation))
    finish()
    if pending is not None:
        unmatched += 1
    return runs, {"malformed_records": malformed, "unmatched_snapshots": unmatched}


def summarize(run):
    total_ms = sum(row[0] for row in run)
    samples = sum(row[1][2] for row in run)
    left = right = 0
    positions = [0.0]
    for _, control, *_ in run:
        left += control[10]
        right += control[11]
        positions.append((left + right) / 2)
    cfg = run[0][3]
    return {
        "duration_seconds": total_ms / 1000,
        "start_tick": (run[0][1][0] - run[0][0]) & 0xFFFFFFFF,
        "end_tick": run[-1][1][0],
        "firmware_marker": run[0][4],
        "motor_compensation": dict(zip(
            ("left_forward_start", "left_forward_run", "left_reverse_start", "left_reverse_run",
             "right_forward_start", "right_forward_run", "right_reverse_start", "right_reverse_run",
             "blend_pwm", "offset_rise_pwm_per_ms", "quiet_ms", "stable_demand_ms",
             "assist_ms", "neutral_rearm_ms", "assist_cooldown_ms"), run[0][5],
        )) if run[0][5] is not None else None,
        "parameters": dict(zip(
            ("Mid_Angle", "Balance_Kp", "Balance_Kd", "Velocity_Kp", "Velocity_Ki",
             "Turn_Kp", "Turn_Kd", "myTurn_Kd", "deadzone", "normal_pwm_limit", "nominal_hz"),
            (cfg[0] / 1000, *(n / 100 for n in cfg[1:8]), *cfg[8:]),
        )),
        "enabled_samples": samples,
        "observed_hz": round(samples * 1000 / total_ms, 2),
        "net_encoder_counts_left_right": [left, right],
        "absolute_encoder_travel_left_right": [sum(r[2][3] for r in run), sum(r[2][4] for r in run)],
        "net_encoder_difference": left - right,
        "position_range_counts_at_window_boundaries": max(positions) - min(positions),
        "common_output_sign_reversals": sum(r[2][7] for r in run),
        "wheel_pwm_reversals_left_right": [sum(r[1][12] for r in run), sum(r[1][13] for r in run)],
        "output_limit_hit_percent_left_right": [
            round(100 * sum(r[2][i] for r in run) / samples, 2) for i in (5, 6)
        ],
        "imu_interval_ms_min_max": [min(r[2][8] for r in run), max(r[2][9] for r in run)],
        "raw_yaw_gyro_mean": round(sum(r[2][10] for r in run) / samples, 3),
        "body_angle_degrees_min_max": [min(r[2][11] for r in run) / 100, max(r[2][12] for r in run) / 100],
        "velocity_integral_snapshot_min_max": [min(r[1][6] for r in run), max(r[1][6] for r in run)],
        "limitations": [
            "Command-free samples do not prove settling: capture after the robot has stood for at least 10 seconds.",
            "Encoder travel assumes no slip; boundary position range can miss movement within each 500 ms window.",
            "Limit hits include exact boundary values and are not necessarily clipped output.",
            "Uncalibrated raw gyro mean and encoder difference are not absolute heading change.",
            "With measured compensation enabled, parameters.deadzone is the legacy rest-only value; motor_compensation describes normal balance.",
        ],
    }


def analyze(text, target_seconds=30):
    runs, diagnostics = parse_windows(text)
    candidates = [r for r in runs if sum(w[0] for w in r) >= 20000]
    if not candidates:
        return {"status": "insufficient_continuous_data", **diagnostics,
                "required": "At least 20 seconds of paired CTRL/OBS command-free windows with FW/CFG markers."}
    # Use the end of the longest contiguous run so initial settling is less likely
    # to dominate. Physical settling still must be confirmed by the operator.
    run = max(candidates, key=lambda r: sum(w[0] for w in r))
    selected = []
    elapsed = 0
    for row in reversed(run):
        if elapsed + row[0] > target_seconds * 1000:
            break
        selected.append(row)
        elapsed += row[0]
    if elapsed < 20000:
        return {"status": "insufficient_continuous_data", **diagnostics,
                "required": "The selected full observation windows must span at least 20 seconds."}
    return {"status": "observed_command_free_window", **diagnostics, **summarize(list(reversed(selected)))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path, help="Existing saved serial log; no hardware access")
    parser.add_argument("--seconds", type=int, choices=range(20, 31), default=30)
    args = parser.parse_args()
    text = args.log.read_bytes().decode("utf-8", errors="replace")
    print(json.dumps(analyze(text, args.seconds), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
