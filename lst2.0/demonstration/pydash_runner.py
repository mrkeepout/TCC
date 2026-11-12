#!/usr/bin/env python3
"""Run the pyDash player and extract lightweight playback QoE telemetry."""

import argparse
import csv
import json
import re
import subprocess
import sys
from pathlib import Path


EVENTS = (
    ("buffer_size", re.compile(r"Execution Time ([\d.]+) > buffer size: (\d+)")),
    ("throughput_bps", re.compile(
        r"Execution Time ([\d.]+) > measured throughput: ([\d.eE+-]+)")),
    ("quality_index", re.compile(
        r"Execution Time ([\d.]+) > selected QI: (\d+)")),
)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--events", required=True, type=Path)
    parser.add_argument("--summary", required=True, type=Path)
    args = parser.parse_args(argv)
    entrypoint = args.source / "main.py"
    if not entrypoint.is_file():
        parser.error(f"pyDash entrypoint not found: {entrypoint}")

    args.log.parent.mkdir(parents=True, exist_ok=True)
    args.events.parent.mkdir(parents=True, exist_ok=True)
    events = []
    summary_lines = {}
    with args.log.open("w", encoding="utf-8") as log, \
            args.events.open("w", newline="", encoding="utf-8") as event_file:
        writer = csv.writer(event_file)
        writer.writerow(("simulation_time_seconds", "event", "value"))
        process = subprocess.Popen(
            [sys.executable, str(entrypoint)],
            cwd=args.source,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        try:
            assert process.stdout is not None
            for line in process.stdout:
                log.write(line)
                log.flush()
                for event_name, pattern in EVENTS:
                    match = pattern.search(line)
                    if match:
                        simulation_time, value = match.groups()
                        writer.writerow((simulation_time, event_name, value))
                        event_file.flush()
                        events.append((event_name, float(value)))
                for key, pattern in (
                    ("pauses_number", r"Pauses number: (\d+)"),
                    ("average_pause_seconds", r"Average Time Pauses: ([\d.]+)"),
                    ("average_quality_index", r"Average QI: ([\d.]+)"),
                ):
                    match = re.search(pattern, line)
                    if match:
                        summary_lines[key] = float(match.group(1))
            return_code = process.wait()
        except KeyboardInterrupt:
            process.terminate()
            return_code = process.wait(timeout=10)

    buffer_samples = [v for k, v in events if k == "buffer_size"]
    throughput_samples = [v for k, v in events if k == "throughput_bps"]
    summary = {
        "return_code": return_code,
        "event_count": len(events),
        "buffer_samples": len(buffer_samples),
        "throughput_samples": len(throughput_samples),
        "minimum_buffer_segments": min(buffer_samples) if buffer_samples else None,
        "mean_throughput_bps": (
            sum(throughput_samples) / len(throughput_samples)
            if throughput_samples else None
        ),
        **summary_lines,
    }
    args.summary.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
