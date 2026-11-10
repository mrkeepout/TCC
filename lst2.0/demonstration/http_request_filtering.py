#!/usr/bin/env python3
"""Passive HTTP request observation and HRF-style decision logging."""

from __future__ import annotations

import ipaddress
import json
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Callable, Deque, Dict, Iterable, Optional


class HRFObserver:
    """Apply the published HRF routing stages without dropping requests.

    The paper describes request-size routing and per-source request-rate
    decisions, but does not publish portable threshold values or an operational
    IAT rule. IAT is therefore recorded as telemetry and is not a classifier.
    """

    def __init__(
        self,
        sink: Callable[[dict], None],
        request_size_threshold_bytes: int,
        requests_per_second_threshold: float,
        rate_window_seconds: float,
        allowlist: Iterable[str] = (),
        blocklist: Iterable[str] = (),
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if request_size_threshold_bytes <= 0:
            raise ValueError("request_size_threshold_bytes must be positive")
        if requests_per_second_threshold <= 0:
            raise ValueError("requests_per_second_threshold must be positive")
        if rate_window_seconds <= 0:
            raise ValueError("rate_window_seconds must be positive")
        self._sink = sink
        self._size_threshold = request_size_threshold_bytes
        self._rate_threshold = requests_per_second_threshold
        self._rate_window = rate_window_seconds
        self._allowlist = tuple(ipaddress.ip_network(v) for v in allowlist)
        self._blocklist = tuple(ipaddress.ip_network(v) for v in blocklist)
        self._clock = clock
        self._lock = threading.Lock()
        self._arrivals: Dict[str, Deque[float]] = defaultdict(deque)
        self._last_arrival: Dict[str, float] = {}

    @staticmethod
    def _matches(ip: str, networks: Iterable) -> bool:
        try:
            address = ipaddress.ip_address(ip)
        except ValueError:
            return False
        return any(address in network for network in networks)

    def observe(
        self,
        source_ip: str,
        method: str,
        target: str,
        header_bytes: int,
        declared_body_bytes: Optional[int],
        complete: bool = True,
        observed_at: Optional[float] = None,
        count_arrival: bool = True,
        event_type: str = "request_start",
    ) -> dict:
        now = self._clock() if observed_at is None else observed_at
        with self._lock:
            arrivals = self._arrivals[source_ip]
            if count_arrival:
                arrivals.append(now)
            cutoff = now - self._rate_window
            while arrivals and arrivals[0] <= cutoff:
                arrivals.popleft()
            request_rate = len(arrivals) / self._rate_window
            previous = self._last_arrival.get(source_ip) if count_arrival else None
            inter_arrival = None if previous is None else max(0.0, now - previous)
            if count_arrival:
                self._last_arrival[source_ip] = now

        is_blocklisted = self._matches(source_ip, self._blocklist)
        is_allowlisted = self._matches(source_ip, self._allowlist)
        request_size = (
            None if declared_body_bytes is None
            else header_bytes + declared_body_bytes
        )
        oversized = (
            request_size is not None and request_size > self._size_threshold
        )
        route_to_analyzer = (
            is_blocklisted or not is_allowlisted or oversized
            or request_size is None or not complete
        )
        if is_blocklisted:
            reason = "blocklist"
        elif route_to_analyzer and request_rate > self._rate_threshold:
            reason = "source_rate"
        elif oversized:
            reason = "request_size_review"
        elif not complete:
            reason = "incomplete_request"
        elif request_size is None:
            reason = "request_size_unavailable"
        elif not is_allowlisted:
            reason = "unknown_source_review"
        else:
            reason = "allowlisted"

        would_block = event_type != "request_complete" and (
            is_blocklisted or (
                route_to_analyzer and request_rate > self._rate_threshold
            )
        )
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": event_type,
            "source_ip": source_ip,
            "method": method,
            "target": target,
            "header_bytes": header_bytes,
            "declared_body_bytes": declared_body_bytes,
            "request_size_bytes": request_size,
            "complete": complete,
            "source_class": (
                "blocklist" if is_blocklisted
                else "allowlist" if is_allowlisted
                else "unknown"
            ),
            "stage": (
                "request_analyzer" if route_to_analyzer
                else "network_analyzer"
            ),
            "requests_per_second": request_rate,
            "inter_arrival_seconds": inter_arrival,
            "decision": "would_block" if would_block else "forward",
            "reason": reason,
            "enforced": False,
        }
        self._sink(event)
        return event

    def parse_error(self, source_ip: str, reason: str) -> None:
        self._sink({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source_ip": source_ip,
            "decision": "uninspected",
            "reason": reason,
            "enforced": False,
        })


class JSONLEventSink:
    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = threading.Lock()

    def __call__(self, event: dict) -> None:
        line = json.dumps(event, sort_keys=True, separators=(",", ":"))
        with self._lock:
            with open(self._path, "a", encoding="utf-8") as output:
                output.write(line + "\n")


class HTTPRequestStream:
    """Observe HTTP/1.x request headers in a TCP byte stream.

    The relay still forwards the original bytes unchanged. Request bodies with
    Content-Length are skipped by the parser; chunked bodies are not decoded and
    are reported with unknown request size.
    """

    def __init__(
        self,
        source_ip: str,
        observer: HRFObserver,
        max_header_bytes: int = 65536,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._source_ip = source_ip
        self._observer = observer
        self._max_header_bytes = max_header_bytes
        self._clock = clock
        self._buffer = bytearray()
        self._header_started_at: Optional[float] = None
        self._body_remaining = 0
        self._chunked_state: Optional[str] = None
        self._chunk_remaining = 0
        self._opaque = False
        self._request_started = False

    def feed(self, data: bytes) -> None:
        if not data or self._opaque:
            return
        if (not self._buffer and not self._body_remaining
                and self._chunked_state is None):
            self._header_started_at = self._clock()
        self._buffer.extend(data)
        while True:
            if self._chunked_state == "size":
                line_end = self._buffer.find(b"\r\n")
                if line_end < 0:
                    return
                chunk_line = bytes(self._buffer[:line_end]).split(b";", 1)[0]
                del self._buffer[:line_end + 2]
                try:
                    self._chunk_remaining = int(chunk_line, 16)
                except ValueError:
                    self._observer.parse_error(
                        self._source_ip, "invalid_chunked_body")
                    self._opaque = True
                    self._buffer.clear()
                    return
                self._chunked_state = (
                    "trailers" if self._chunk_remaining == 0 else "data"
                )
                continue
            if self._chunked_state == "data":
                consumed = min(self._chunk_remaining, len(self._buffer))
                del self._buffer[:consumed]
                self._chunk_remaining -= consumed
                if self._chunk_remaining:
                    return
                if len(self._buffer) < 2:
                    return
                if self._buffer[:2] != b"\r\n":
                    self._observer.parse_error(
                        self._source_ip, "invalid_chunked_body_terminator")
                    self._opaque = True
                    self._buffer.clear()
                    return
                del self._buffer[:2]
                self._chunked_state = "size"
                if self._buffer:
                    self._header_started_at = self._clock()
                continue
            if self._chunked_state == "trailers":
                if self._buffer.startswith(b"\r\n"):
                    del self._buffer[:2]
                else:
                    trailers_end = self._buffer.find(b"\r\n\r\n")
                    if trailers_end < 0:
                        return
                    del self._buffer[:trailers_end + 4]
                self._chunked_state = None
                if self._buffer:
                    self._header_started_at = self._clock()
                continue
            if self._body_remaining:
                consumed = min(self._body_remaining, len(self._buffer))
                del self._buffer[:consumed]
                self._body_remaining -= consumed
                if self._body_remaining:
                    return
                if self._buffer:
                    self._header_started_at = self._clock()
                continue

            boundary = self._buffer.find(b"\r\n\r\n")
            if boundary < 0:
                if len(self._buffer) > self._max_header_bytes:
                    self._observer.parse_error(
                        self._source_ip, "request_header_exceeds_limit"
                    )
                    self._buffer.clear()
                    self._header_started_at = None
                    self._request_started = False
                    continue
                if not self._request_started:
                    line_end = self._buffer.find(b"\r\n")
                    if line_end >= 0:
                        tokens = bytes(self._buffer[:line_end]).decode(
                            "iso-8859-1", "replace").split()
                        if len(tokens) >= 2:
                            self._observer.observe(
                                self._source_ip,
                                tokens[0],
                                tokens[1],
                                line_end + 2,
                                None,
                                complete=False,
                                observed_at=self._header_started_at,
                                event_type="request_start",
                            )
                            self._request_started = True
                return

            header_bytes = boundary + 4
            if header_bytes > self._max_header_bytes:
                self._observer.parse_error(
                    self._source_ip, "request_header_exceeds_limit")
                del self._buffer[:header_bytes]
                self._header_started_at = None
                self._request_started = False
                continue
            raw_header = bytes(self._buffer[:boundary])
            del self._buffer[:header_bytes]
            started_at = self._header_started_at or self._clock()
            self._header_started_at = None
            lines = raw_header.split(b"\r\n")
            request_line = lines[0].decode("iso-8859-1", "replace")
            tokens = request_line.split()
            if len(tokens) < 2:
                continue

            headers = {}
            for line in lines[1:]:
                name, separator, value = line.partition(b":")
                if separator:
                    headers[name.decode("iso-8859-1").strip().lower()] = (
                        value.decode("iso-8859-1").strip()
                    )

            body_bytes: Optional[int]
            transfer_encoding = headers.get("transfer-encoding", "").lower()
            if "chunked" in transfer_encoding:
                body_bytes = None
                self._chunked_state = "size"
            else:
                try:
                    body_bytes = int(headers.get("content-length", "0"))
                    if body_bytes < 0:
                        body_bytes = None
                except ValueError:
                    body_bytes = None
            self._observer.observe(
                self._source_ip,
                tokens[0],
                tokens[1],
                header_bytes,
                body_bytes,
                observed_at=started_at,
                count_arrival=not self._request_started,
                event_type=(
                    "request_complete" if self._request_started
                    else "request_start"
                ),
            )
            self._request_started = False
            if body_bytes:
                self._body_remaining = body_bytes
            elif self._chunked_state is None and self._buffer:
                self._header_started_at = self._clock()

    def close(self) -> None:
        if self._body_remaining or self._chunked_state is not None or self._opaque:
            return
        if not self._buffer:
            return
        partial = bytes(self._buffer)
        first_line, separator, _ = partial.partition(b"\r\n")
        tokens = first_line.decode("iso-8859-1", "replace").split()
        if separator and len(tokens) >= 2 and not self._request_started:
            self._observer.observe(
                self._source_ip,
                tokens[0],
                tokens[1],
                len(partial),
                None,
                complete=False,
                observed_at=self._header_started_at,
                event_type="request_start",
            )
        self._buffer.clear()
