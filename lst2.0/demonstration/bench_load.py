#!/usr/bin/env python3
# Copyright (C) 2022 Alexandre Mitsuru Kaihara
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.

# Brief: Closed-loop HTTP load generator for ONE concurrency level, used by
# edos_benchmark.py to find a single web replica's capacity. `concurrency`
# worker threads each hold a Keep-Alive connection and issue back-to-back GETs
# for `duration` seconds -- so `concurrency` equals the number of simultaneous
# connections pressed onto the server (what Apache's MaxRequestWorkers caps).
# Pure stdlib (no ab/wrk), so it runs in the plain client container. Prints one
# JSON object with success rate and latency percentiles to stdout.
#
# Usage: bench_load.py --target <ip> --port 80 --concurrency 50 --duration 8

import argparse
import http.client
import json
import sys
import threading
import time


def _worker(target, port, path, deadline, timeout, out, lock):
    conn = None
    while time.time() < deadline:
        t0 = time.time()
        ok = False
        try:
            if conn is None:
                conn = http.client.HTTPConnection(target, port, timeout=timeout)
            conn.request('GET', path, headers={'Connection': 'keep-alive'})
            resp = conn.getresponse()
            resp.read()
            ok = resp.status < 500
        except Exception:
            ok = False
            try:
                conn.close()
            except Exception:
                pass
            conn = None
        latency_ms = (time.time() - t0) * 1000.0
        with lock:
            out.append((ok, latency_ms))
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass


def _percentile(sorted_vals, q):
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = q / 100.0 * (len(sorted_vals) - 1)
    lo = int(pos)
    frac = pos - lo
    if lo + 1 >= len(sorted_vals):
        return sorted_vals[-1]
    return sorted_vals[lo] + (sorted_vals[lo + 1] - sorted_vals[lo]) * frac


def run_level(target, port, path, concurrency, duration, timeout=5.0):
    """Drive `concurrency` persistent connections for `duration` seconds and
    return the measured stats for this level."""
    out, lock = [], threading.Lock()
    deadline = time.time() + duration
    threads = [threading.Thread(target=_worker,
                                args=(target, port, path, deadline, timeout, out, lock),
                                daemon=True)
               for _ in range(concurrency)]
    t0 = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=duration + timeout + 5)
    elapsed = max(time.time() - t0, 1e-6)

    total = len(out)
    ok = sum(1 for o, _ in out if o)
    lat = sorted(ms for _, ms in out)
    return {
        'concurrency': concurrency,
        'requests': total,
        'ok': ok,
        'fail': total - ok,
        'success_rate': (ok / total) if total else 0.0,
        'rps': total / elapsed,
        'p50_ms': _percentile(lat, 50),
        'p95_ms': _percentile(lat, 95),
        'p99_ms': _percentile(lat, 99),
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--target', required=True)
    p.add_argument('--port', type=int, default=80)
    p.add_argument('--path', default='/')
    p.add_argument('--concurrency', type=int, required=True)
    p.add_argument('--duration', type=float, default=8.0)
    p.add_argument('--timeout', type=float, default=5.0)
    a = p.parse_args()
    stats = run_level(a.target, a.port, a.path, a.concurrency, a.duration, a.timeout)
    print(json.dumps(stats))


if __name__ == '__main__':
    main()
