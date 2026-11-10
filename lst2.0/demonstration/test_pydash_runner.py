import json
import tempfile
from pathlib import Path

from pydash_runner import main


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


if __name__ == "__main__":
    test_runner_captures_buffer_throughput_and_player_summary()
    print("ok   test_runner_captures_buffer_throughput_and_player_summary")
