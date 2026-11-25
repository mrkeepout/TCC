import json
import os
import signal
import tempfile
import threading
from pathlib import Path

import pytest

from pydash_runner import (
    PAUSE_PATCHES, SHAPING_OFF_PATCH, URL_PATCH, main, stage_pydash_source)


def _run(root, script):
    source = root / "pydash"
    source.mkdir()
    (source / "main.py").write_text(script, encoding="utf-8")
    paths = {name: root / name for name in ("player.log", "events.csv", "qoe.json")}
    return_code = main([
        "--source", str(source),
        "--log", str(paths["player.log"]),
        "--events", str(paths["events.csv"]),
        "--summary", str(paths["qoe.json"]),
    ])
    summary = json.loads(paths["qoe.json"].read_text(encoding="utf-8"))
    return return_code, summary, paths


def test_runner_captures_buffer_throughput_and_player_summary():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source = root / "pydash"
        source.mkdir()
        (source / "main.py").write_text(
            "print('Execution Time 1.0 > buffer size: 4')\n"
            "print('Execution Time 1.5 > measured throughput: 8000')\n"
            "print('Pauses number: 2')\n"
            "print('Average Time Pauses: 1.25')\n"
            "print('Average QI: 1200')\n",
            encoding="utf-8",
        )
        log = root / "player.log"
        events = root / "events.csv"
        summary_path = root / "qoe.json"
        return_code = main([
            "--source", str(source),
            "--log", str(log),
            "--events", str(events),
            "--summary", str(summary_path),
        ])
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        assert return_code == 0
        assert summary["pauses_number"] == 2
        assert summary["average_pause_seconds"] == 1.25
        assert summary["buffer_samples"] == 1
        assert summary["minimum_buffer_segments"] == 4
        assert summary["mean_throughput_bps"] == 8000
        assert "throughput_bps" in events.read_text(encoding="utf-8")


def test_runner_replaces_invalid_utf8_and_still_writes_summary():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source = root / "pydash"
        source.mkdir()
        (source / "main.py").write_text(
            "import sys\n"
            "sys.stdout.buffer.write(b'library output: \\x88\\n')\n"
            "sys.stdout.buffer.flush()\n"
            "print('Execution Time 2.0 > buffer size: 3')\n"
            "print('Execution Time 2.5 > measured throughput: 6400')\n",
            encoding="utf-8",
        )
        log = root / "player.log"
        events = root / "events.csv"
        summary_path = root / "qoe.json"
        return_code = main([
            "--source", str(source),
            "--log", str(log),
            "--events", str(events),
            "--summary", str(summary_path),
        ])
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        assert return_code == 0
        assert "\ufffd" in log.read_text(encoding="utf-8")
        assert summary["buffer_samples"] == 1
        assert summary["mean_throughput_bps"] == 6400


def test_runner_counts_pauses_from_patched_player_events():
    with tempfile.TemporaryDirectory() as directory:
        return_code, summary, paths = _run(Path(directory), (
            "print('Execution Time 5.0 > buffer size: 0')\n"
            "print('Execution Time 6.0 > pause started')\n"
            "print('Execution Time 9.0 > pause ended: 3.0')\n"
            "print('Execution Time 20.0 > pause started')\n"
            "print('Execution Time 21.0 > pause ended: 1.0')\n"
            "print('Execution Time 22.0 > measured throughput: 1000')\n"
        ))
        assert return_code == 0
        assert summary["pauses_number"] == 2
        assert summary["average_pause_seconds"] == 2.0
        assert summary["total_pause_seconds"] == 4.0
        # pyDash never reached its finalization: not a complete playback.
        assert summary["completed"] is False
        assert summary["stopped_by_testbed"] is False
        assert summary["last_execution_time_seconds"] == 22.0
        assert "pause_started" in paths["events.csv"].read_text(encoding="utf-8")


def test_runner_marks_finished_playback_complete():
    with tempfile.TemporaryDirectory() as directory:
        _, summary, _ = _run(Path(directory), (
            "print('Execution Time 1.0 > pause started')\n"
            "print('Execution Time 2.0 > pause ended: 1.0')\n"
            "print('Pauses number: 1')\n"
        ))
        assert summary["completed"] is True
        assert summary["pauses_number"] == 1


def test_sigterm_stops_player_and_writes_partial_summary():
    # Teardown sends SIGTERM to the runner; it must stop the player and still
    # write the summary, counting the pause that was open at that moment.
    with tempfile.TemporaryDirectory() as directory:
        timer = threading.Timer(
            2.0, os.kill, args=(os.getpid(), signal.SIGTERM))
        timer.start()
        try:
            _, summary, _ = _run(Path(directory), (
                "import time\n"
                "print('Execution Time 1.0 > buffer size: 2')\n"
                "print('Execution Time 1.5 > measured throughput: 5000')\n"
                "print('Execution Time 3.0 > pause started')\n"
                "print('Execution Time 7.0 > buffer size: 0')\n"
                "time.sleep(60)\n"
            ))
        finally:
            timer.cancel()
        assert summary["stopped_by_testbed"] is True
        assert summary["completed"] is False
        assert summary["pauses_number"] == 1
        assert summary["total_pause_seconds"] == 4.0
        assert summary["segments_downloaded"] == 1


def _fake_checkout(root, player_extra=""):
    source = root / "checkout"
    (source / "player").mkdir(parents=True)
    (source / "connection").mkdir()
    (source / "r2a").mkdir()
    (source / ".git").mkdir()
    (source / "main.py").write_text("", encoding="utf-8")
    (source / "player" / "player.py").write_text(
        "\n".join(original for _, original, _ in (URL_PATCH, *PAUSE_PATCHES))
        + "\n" + player_extra, encoding="utf-8")
    (source / "connection" / "connection_handler.py").write_text(
        SHAPING_OFF_PATCH[1] + "\n", encoding="utf-8")
    (source / "r2a" / "r2a_averagethroughput.py").write_text(
        "class R2A_AverageThroughput(IR2A):\n    pass\n", encoding="utf-8")
    (source / "dash_client.json").write_text(json.dumps({
        "url_mpd": "http://example.invalid/x.mpd",
        "r2a_algorithm": "R2AFixed", "max_buffer_size": 60}), encoding="utf-8")
    return source


def test_stage_configures_and_patches_checkout():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source = _fake_checkout(root)
        staged = root / "staged"
        stage_pydash_source(
            source, staged, mpd_url="http://192.168.100.10/dash/1sec/a.mpd",
            r2a_algorithm="R2A_AverageThroughput", traffic_shaping=False)
        config = json.loads((staged / "dash_client.json").read_text())
        assert config["url_mpd"] == "http://192.168.100.10/dash/1sec/a.mpd"
        assert config["r2a_algorithm"] == "R2A_AverageThroughput"
        assert config["max_buffer_size"] == 60
        player = (staged / "player" / "player.py").read_text()
        for _, _, replacement in (URL_PATCH, *PAUSE_PATCHES):
            assert replacement in player
        handler = (staged / "connection" / "connection_handler.py").read_text()
        assert handler.startswith("if True:")
        assert not (staged / ".git").exists()
        # The source checkout itself is never modified.
        assert "pause started" not in (source / "player" / "player.py").read_text()


def test_stage_keeps_shaping_when_requested():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        staged = root / "staged"
        stage_pydash_source(
            _fake_checkout(root), staged, mpd_url="http://x/a.mpd",
            r2a_algorithm="R2A_AverageThroughput", traffic_shaping=True)
        handler = (staged / "connection" / "connection_handler.py").read_text()
        assert handler == SHAPING_OFF_PATCH[1] + "\n"


def test_stage_rejects_unknown_pydash_version_and_algorithm():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source = _fake_checkout(root, player_extra=PAUSE_PATCHES[0][1])
        with pytest.raises(RuntimeError, match="Unsupported pyDash version"):
            stage_pydash_source(
                source, root / "a", mpd_url="http://x/a.mpd",
                r2a_algorithm="R2A_AverageThroughput", traffic_shaping=False)
        with pytest.raises(ValueError, match="not found"):
            stage_pydash_source(
                source, root / "b", mpd_url="http://x/a.mpd",
                r2a_algorithm="R2ANope", traffic_shaping=False)


@pytest.mark.skipif(not os.environ.get("PYDASH_SOURCE_DIR"),
                    reason="needs a real pyDash checkout in PYDASH_SOURCE_DIR")
def test_stage_applies_to_real_pydash_checkout():
    with tempfile.TemporaryDirectory() as directory:
        for algorithm in ("R2A_AverageThroughput", "R2AFixed", "R2ARandom"):
            stage_pydash_source(
                os.environ["PYDASH_SOURCE_DIR"], Path(directory) / algorithm,
                mpd_url="http://192.168.100.10/dash/1sec/a.mpd",
                r2a_algorithm=algorithm, traffic_shaping=False)
