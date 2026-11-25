#!/usr/bin/env python3
"""Run the pyDash player and extract lightweight playback QoE telemetry.

This module has two halves:

* stage_pydash_source() runs on the HOST (imported by edos.py). It copies a
  pyDash checkout, points it at the testbed VIP, picks the ABR (R2A) algorithm
  and applies the small source patches the testbed relies on.
* main() runs INSIDE the client container. It starts the staged player, keeps
  its log, turns the player's lines into an events CSV and writes a QoE summary
  when the player ends -- or when the testbed stops it (SIGTERM), so a run that
  ends before the video does still yields a (partial) summary.

The pause events parsed by main() exist only because stage_pydash_source()
patches them into the player, which is why both live in the same module.
"""

import argparse
import csv
import json
import re
import shutil
import signal
import statistics
import subprocess
import sys
from pathlib import Path


EVENTS = (
    ("buffer_size", re.compile(r"Execution Time ([\d.]+) > buffer size: (\d+)")),
    ("throughput_bps", re.compile(
        r"Execution Time ([\d.]+) > measured throughput: ([\d.eE+-]+)")),
    ("quality_index", re.compile(
        r"Execution Time ([\d.]+) > selected QI: (\d+)")),
    ("pause_ended_seconds", re.compile(
        r"Execution Time ([\d.]+) > pause ended: ([\d.eE+-]+)")),
)
PAUSE_STARTED = re.compile(r"Execution Time ([\d.]+) > pause started")
EXECUTION_TIME = re.compile(r"Execution Time ([\d.]+)")
# Printed by pyDash's Player.finalization(), i.e. only when the video ended.
PLAYER_SUMMARY = (
    ("pauses_number", re.compile(r"Pauses number: (\d+)")),
    ("average_pause_seconds", re.compile(r"Average Time Pauses: ([\d.]+)")),
    ("average_quality_index", re.compile(r"Average QI: ([\d.]+)")),
)

STAGING_IGNORE = shutil.ignore_patterns(
    ".git", "venv", "__pycache__", "*.pyc", "results")

# (file, original, replacement). Each original must occur exactly once in the
# checkout, otherwise the pyDash version is not the one these patches target
# and staging fails instead of silently running an unpatched player.
URL_PATCH = (
    "player/player.py",
    "segment_request.add_path_name('/'.join(url_tokens[:len(url_tokens) - 1]))",
    "segment_request.add_path_name('/' + '/'.join(url_tokens[3:-1]))",
)
# pyDash only reports pauses at the very end of the video. These two prints
# expose each pause as it happens, with pyDash's own definition (a playback
# tick that finds the buffer empty starts a pause; the next tick that plays
# ends it), so a run interrupted mid-video can still be summarised.
PAUSE_PATCHES = (
    (
        "player/player.py",
        "self.pauses_number += 1",
        "self.pauses_number += 1; "
        "print(f'Execution Time {current_time} > pause started')",
    ),
    (
        "player/player.py",
        "self.playback_pauses.add(current_time, pause_time)",
        "self.playback_pauses.add(current_time, pause_time); "
        "print(f'Execution Time {current_time} > pause ended: {pause_time}')",
    ),
)
# pyDash emulates a network by sleeping after each download, cycling every few
# seconds through bandwidths taken from the MPD itself (top, middle and lowest
# bitrate). Inside the testbed that emulation would hide the real effect of the
# attack, so by default it is switched off and the testbed network is the only
# limit.
SHAPING_OFF_PATCH = (
    "connection/connection_handler.py",
    "if package_size == 0:",
    "if True:  # LST2.0: pyDash traffic shaping disabled, "
    "the testbed network is the only limit",
)


def check_r2a_algorithm(source, name):
    """pyDash loads r2a.<name.lower()> and takes the class <name> from it."""
    module = Path(source) / "r2a" / f"{name.lower()}.py"
    if not module.is_file():
        raise ValueError(
            f"R2A algorithm {name!r} not found: pyDash would import {module}")
    if not re.search(rf"^class {re.escape(name)}\(", module.read_text(
            encoding="utf-8"), re.MULTILINE):
        raise ValueError(f"{module} does not define class {name}")


def stage_pydash_source(source, destination, *, mpd_url, r2a_algorithm,
                        traffic_shaping):
    """Copy a pyDash checkout to destination, configured and patched."""
    source = Path(source)
    destination = Path(destination)
    check_r2a_algorithm(source, r2a_algorithm)
    shutil.copytree(source, destination, ignore=STAGING_IGNORE)

    patches = [URL_PATCH, *PAUSE_PATCHES]
    if not traffic_shaping:
        patches.append(SHAPING_OFF_PATCH)
    for relative, original, replacement in patches:
        path = destination / relative
        text = path.read_text(encoding="utf-8")
        if text.count(original) != 1:
            raise RuntimeError(
                f"Unsupported pyDash version: expected exactly one "
                f"{original!r} in {relative}")
        path.write_text(text.replace(original, replacement, 1),
                        encoding="utf-8")

    with open(source / "dash_client.json", encoding="utf-8") as config_file:
        dash_config = json.load(config_file)
    dash_config["url_mpd"] = mpd_url
    dash_config["r2a_algorithm"] = r2a_algorithm
    (destination / "dash_client.json").write_text(
        json.dumps(dash_config, indent=2) + "\n", encoding="utf-8")


def summarize(events, pause_starts, last_time, player_summary, return_code,
              stopped):
    buffer_samples = [v for k, v in events if k == "buffer_size"]
    throughput_samples = [v for k, v in events if k == "throughput_bps"]
    pause_durations = [v for k, v in events if k == "pause_ended_seconds"]
    # A pause still open when the player was stopped counts up to the last
    # line the player printed.
    ongoing_pause = 0.0
    if len(pause_starts) > len(pause_durations) and last_time is not None:
        ongoing_pause = max(0.0, last_time - pause_starts[-1])
    summary = {
        "return_code": return_code,
        # The player reached its finalization, i.e. played the whole video.
        "completed": "pauses_number" in player_summary,
        "stopped_by_testbed": stopped,
        "last_execution_time_seconds": last_time,
        "event_count": len(events),
        "buffer_samples": len(buffer_samples),
        "throughput_samples": len(throughput_samples),
        "segments_downloaded": len(throughput_samples),
        "minimum_buffer_segments": min(buffer_samples) if buffer_samples else None,
        "mean_throughput_bps": (
            statistics.mean(throughput_samples) if throughput_samples else None),
        "pauses_number": len(pause_starts),
        "average_pause_seconds": (
            statistics.mean(pause_durations) if pause_durations else None),
        "total_pause_seconds": sum(pause_durations) + ongoing_pause,
    }
    # When the video ended, pyDash's own end-of-run numbers are authoritative.
    summary.update(player_summary)
    return summary


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
    pause_starts = []
    last_time = None
    player_summary = {}
    process = None
    stopped = False

    def stop_player(signum, frame):
        # The testbed sends SIGTERM at teardown: end the player and fall
        # through to the summary instead of dying without one.
        nonlocal stopped
        stopped = True
        if process is not None and process.poll() is None:
            process.terminate()

    previous_handler = signal.signal(signal.SIGTERM, stop_player)
    try:
        with args.log.open("w", encoding="utf-8") as log, \
                args.events.open("w", newline="", encoding="utf-8") as event_file:
            writer = csv.writer(event_file)
            writer.writerow(("simulation_time_seconds", "event", "value"))
            event_file.flush()
            process = subprocess.Popen(
                # -u: the player's stdout is a pipe; unbuffered output keeps
                # the log and events current if the player is stopped.
                [sys.executable, "-u", str(entrypoint)],
                cwd=args.source,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            if stopped:
                process.terminate()
            try:
                assert process.stdout is not None
                for line in process.stdout:
                    log.write(line)
                    log.flush()
                    match = EXECUTION_TIME.search(line)
                    if match:
                        last_time = float(match.group(1))
                    match = PAUSE_STARTED.search(line)
                    if match:
                        pause_starts.append(float(match.group(1)))
                        writer.writerow((match.group(1), "pause_started", ""))
                        event_file.flush()
                    for event_name, pattern in EVENTS:
                        match = pattern.search(line)
                        if match:
                            simulation_time, value = match.groups()
                            writer.writerow((simulation_time, event_name, value))
                            event_file.flush()
                            events.append((event_name, float(value)))
                    for key, pattern in PLAYER_SUMMARY:
                        match = pattern.search(line)
                        if match:
                            value = float(match.group(1))
                            player_summary[key] = (
                                int(value) if key == "pauses_number" else value)
                return_code = process.wait()
            except KeyboardInterrupt:
                stopped = True
                process.terminate()
                return_code = process.wait(timeout=10)
    finally:
        signal.signal(signal.SIGTERM, previous_handler)

    summary = summarize(events, pause_starts, last_time, player_summary,
                        return_code, stopped)
    args.summary.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
