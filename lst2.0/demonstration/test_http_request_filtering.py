import json
import socket
import tempfile
import threading
from pathlib import Path

from http_request_filtering import HRFObserver, HTTPRequestStream, JSONLEventSink
from loadbalancer.lb import pipe


def _observer(events, **overrides):
    settings = {
        "request_size_threshold_bytes": 1000,
        "requests_per_second_threshold": 2,
        "rate_window_seconds": 1,
        "allowlist": ["192.168.100.51/32"],
    }
    settings.update(overrides)
    return HRFObserver(events.append, **settings)


def test_allowlisted_small_request_is_forwarded_without_enforcement():
    events = []
    event = _observer(events).observe(
        "192.168.100.51", "GET", "/manifest.mpd", 100, 0,
        observed_at=1.0,
    )
    assert event["source_class"] == "allowlist"
    assert event["stage"] == "network_analyzer"
    assert event["decision"] == "forward"
    assert event["enforced"] is False


def test_unknown_source_is_routed_to_request_analyzer():
    event = _observer([]).observe(
        "192.168.100.103", "GET", "/", 100, 0, observed_at=1.0,
    )
    assert event["source_class"] == "unknown"
    assert event["stage"] == "request_analyzer"
    assert event["reason"] == "unknown_source_review"


def test_rate_threshold_marks_shadow_candidate_but_never_blocks():
    observer = _observer([])
    first = observer.observe(
        "192.168.100.103", "GET", "/", 100, 0, observed_at=1.0,
    )
    second = observer.observe(
        "192.168.100.103", "GET", "/", 100, 0, observed_at=1.2,
    )
    third = observer.observe(
        "192.168.100.103", "GET", "/", 100, 0, observed_at=1.3,
    )
    assert first["decision"] == second["decision"] == "forward"
    assert third["requests_per_second"] == 3
    assert third["decision"] == "would_block"
    assert third["reason"] == "source_rate"
    assert third["enforced"] is False


def test_large_allowlisted_request_is_escalated_for_review():
    event = _observer([]).observe(
        "192.168.100.51", "POST", "/upload", 100, 2000, observed_at=1.0,
    )
    assert event["stage"] == "request_analyzer"
    assert event["reason"] == "request_size_review"
    assert event["decision"] == "forward"


def test_stream_parser_handles_split_headers_content_length_and_pipeline():
    events = []
    stream = HTTPRequestStream(
        "192.168.100.103", _observer(events),
        clock=iter([1.0, 1.1, 1.2, 1.3]).__next__,
    )
    stream.feed(b"POST /upload HTTP/1.1\r\nContent-Len")
    stream.feed(b"gth: 4\r\n\r\nDATAGET /next HTTP/1.1\r\nHost: test\r\n\r\n")
    starts = [event for event in events
              if event["event_type"] == "request_start"]
    completed = [event for event in events
                 if event["event_type"] == "request_complete"]
    assert [event["target"] for event in starts] == ["/upload", "/next"]
    assert len(completed) == 1
    assert completed[0]["request_size_bytes"] == len(
        b"POST /upload HTTP/1.1\r\nContent-Length: 4\r\n\r\nDATA")
    assert starts[1]["method"] == "GET"


def test_stream_parser_skips_chunked_body_and_continues():
    events = []
    stream = HTTPRequestStream("192.168.100.103", _observer(events))
    stream.feed(
        b"POST /chunked HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n"
        b"7d0\r\n")
    stream.feed(b"x" * 1000)
    assert not stream._buffer
    stream.feed(
        b"x" * 1000 + b"\r\n0\r\n\r\n"
        b"GET /next HTTP/1.1\r\nHost: test\r\n\r\n")
    starts = [event for event in events
              if event["event_type"] == "request_start"]
    assert [event["target"] for event in starts] == ["/chunked", "/next"]
    assert starts[0]["request_size_bytes"] is None


def test_close_records_incomplete_slow_request():
    events = []
    stream = HTTPRequestStream("192.168.100.103", _observer(events))
    stream.feed(b"GET / HTTP/1.1\r\nHost: test\r\nX-Slow: ")
    stream.close()
    assert len(events) == 1
    assert events[0]["complete"] is False
    assert events[0]["reason"] == "incomplete_request"


def test_slow_header_is_counted_at_request_line_without_double_counting():
    events = []
    observer = _observer(
        events, requests_per_second_threshold=1, allowlist=["192.168.100.51/32"])
    first = HTTPRequestStream("192.168.100.51", observer)
    second = HTTPRequestStream("192.168.100.51", observer)
    first.feed(b"GET /one HTTP/1.1\r\nHost: test\r\n")
    second.feed(b"GET /two HTTP/1.1\r\nHost: test\r\n")
    assert events[0]["event_type"] == "request_start"
    assert events[1]["decision"] == "would_block"
    first.feed(b"\r\n")
    assert events[-1]["event_type"] == "request_complete"
    assert sum(event["event_type"] == "request_start" for event in events) == 2
    first.close()
    second.close()
    assert sum(event["event_type"] == "request_start" for event in events) == 2


def test_event_sink_writes_one_json_object_per_line():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "events.jsonl"
        sink = JSONLEventSink(str(path))
        sink({"decision": "forward"})
        sink({"decision": "would_block"})
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        assert [row["decision"] for row in rows] == ["forward", "would_block"]


def test_proxy_observation_preserves_relayed_bytes():
    events = []
    observer = _observer(events)
    inspector = HTTPRequestStream("192.168.100.103", observer)
    client_side, proxy_source = socket.socketpair()
    proxy_destination, server_side = socket.socketpair()
    worker = threading.Thread(target=pipe, args=(
        proxy_source, proxy_destination, inspector))
    worker.start()
    request = b"GET /dash/manifest.mpd HTTP/1.1\r\nHost: test\r\n\r\n"
    client_side.sendall(request)
    client_side.shutdown(socket.SHUT_WR)
    received = bytearray()
    while True:
        part = server_side.recv(1024)
        if not part:
            break
        received.extend(part)
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert bytes(received) == request
    assert events[0]["target"] == "/dash/manifest.mpd"
    client_side.close()
    server_side.close()


if __name__ == "__main__":
    tests = [value for name, value in globals().items()
             if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"ok   {test.__name__}")
    print(f"\n{len(tests)}/{len(tests)} passed")
