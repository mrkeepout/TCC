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

# Brief: Benign background traffic generator. Runs INSIDE a client container and
# issues well-formed HTTP GETs to the load balancer VIP at a low, steady rate,
# so captured flows contain a benign/malicious contrast for later analysis.
#
# Usage: legit_client.py <target_vip> <port> [interval_seconds]

import random
import sys
import time
import urllib.request


def main():
    vip = sys.argv[1]
    port = int(sys.argv[2])
    interval = float(sys.argv[3]) if len(sys.argv) > 3 else 3.0
    url = f"http://{vip}:{port}/"

    ok = 0
    fail = 0
    last_err = ''
    while True:
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                resp.read()
            ok += 1
        except Exception as ex:
            # Don't stop on errors, but DON'T swallow them silently either: a
            # saturated backend makes every request fail, which used to look like
            # "no traffic at all". Surface counts so that's diagnosable.
            fail += 1
            last_err = f"{type(ex).__name__}: {ex}"
        if (ok + fail) % 10 == 0:
            sys.stderr.write(
                f"[legit] target={url} ok={ok} fail={fail} last_err={last_err}\n")
            sys.stderr.flush()
        # Jitter so requests don't look robotic.
        time.sleep(interval * random.uniform(0.5, 1.5))


if __name__ == '__main__':
    main()
