#!/usr/bin/env python
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

# Brief: Replica capacity benchmark -- the GROUND TRUTH behind the autoscaler
# thresholds. Brings up a minimal topology (bridge + controller + ONE web
# replica + one client), then:
#   1. reads the replica's CONFIGURED ceiling (Apache MaxRequestWorkers), and
#   2. sweeps simultaneous-connection levels (bench_load.py) to find the
#      empirical saturation KNEE -- the highest concurrency still meeting the
#      success + p95-latency SLO.
# From the knee it recommends SCALE_UP_CONN / SCALE_DOWN_CONN as a target-
# utilisation fraction of capacity, so the thresholds stop being guesses.
#
# Output: flows/benchmark.json + a printed report with suggested config lines.
# Run as root (like edos.py): sudo python3 edos_benchmark.py

import json
import re
import signal
import subprocess
import sys
import time

from host import Host
from switch import Switch
from controller import Controller

import edos_config as cfg

nodes = {}


def printTerm(s):
    subprocess.run(f'echo "{s}"', shell=True)


# --- Topology (minimal subset of edos.py) --------------------------------
def createBridge(name):
    printTerm(f"[benchmark] Creating switch {name}")
    nodes[name] = Switch(name, __import__('os').getcwd() + '/flows/' + name, '/home/pcap')
    nodes[name].instantiate()
    nodes[name].setIp(cfg.br_ip, cfg.mask)
    nodes[name].connectToInternet(cfg.gateway, cfg.mask)


def createController(name, bridgeName):
    printTerm(f"[benchmark] Creating controller {name}")
    nodes[name] = Controller(name)
    nodes[name].instantiate()
    nodes[name].connect(nodes[bridgeName])
    nodes[name].setIp(cfg.controller_ip, cfg.mask, nodes[bridgeName])
    nodes[name].initController(cfg.controller_ip, cfg.controller_port)
    nodes[bridgeName].setController(cfg.controller_ip, cfg.controller_port)


def attachHost(node, ip):
    node.connect(nodes['br0'])
    node.setIp(ip, cfg.mask, nodes['br0'])
    node.setDefaultGateway(cfg.gateway, nodes['br0'])


# --- Pure helpers (unit-tested offline) ----------------------------------
def parse_capacity(text):
    """Pull the configured worker ceiling (MaxRequestWorkers / MaxClients) from
    an Apache config dump. Returns int or None."""
    m = re.search(r'(?:MaxRequestWorkers|MaxClients)\s+(\d+)', text or '', re.I)
    return int(m.group(1)) if m else None


def healthy(level, slo_success, slo_p95_ms):
    return (level.get('success_rate', 0) >= slo_success
            and level.get('p95_ms', float('inf')) <= slo_p95_ms)


def find_knee(sweep, slo_success, slo_p95_ms):
    """The knee is the highest concurrency level still meeting the SLO. Returns
    (knee_level_or_None, first_unhealthy_level_or_None)."""
    knee = None
    first_bad = None
    for lvl in sorted(sweep, key=lambda x: x['concurrency']):
        if healthy(lvl, slo_success, slo_p95_ms):
            knee = lvl
        elif first_bad is None:
            first_bad = lvl
    return knee, first_bad


def recommend(knee_concurrency, target_util, down_util):
    up = max(1, round(knee_concurrency * target_util))
    down = max(1, round(knee_concurrency * down_util))
    if down >= up:
        down = max(1, up - 1)
    return {'SCALE_UP_CONN': up, 'SCALE_DOWN_CONN': down}


# --- Measurement ----------------------------------------------------------
def read_configured_capacity(web):
    """Best-effort read of Apache's configured worker ceiling from the replica."""
    probes = [
        "bash -c \"apache2ctl -t -D DUMP_RUN_CFG 2>/dev/null | grep -i MaxRequestWorkers\"",
        "bash -c \"grep -rhiE 'MaxRequestWorkers|MaxClients' /etc/apache2 2>/dev/null\"",
        "bash -c \"httpd -V 2>/dev/null; apachectl -V 2>/dev/null\"",
    ]
    for cmd in probes:
        out = web.run(cmd)
        text = (getattr(out, 'stdout', b'') or b'').decode('utf8', 'ignore')
        cap = parse_capacity(text)
        if cap:
            return cap, text.strip().splitlines()[:3]
    return None, []


def run_sweep(client, web_ip):
    levels = getattr(cfg, 'BENCH_LEVELS', [10, 25, 50, 75, 100, 150, 200])
    duration = getattr(cfg, 'BENCH_DURATION', 8)
    slo_s = getattr(cfg, 'BENCH_SLO_SUCCESS', 0.99)
    slo_p = getattr(cfg, 'BENCH_SLO_P95_MS', 500)
    stop_after = getattr(cfg, 'BENCH_STOP_AFTER_FAILS', 2)

    sweep = []
    consecutive_bad = 0
    for c in levels:
        printTerm(f"[benchmark] level concurrency={c} for {duration}s ...")
        out = client.run(
            f"bash -c \"python3 /home/bench_load.py --target {web_ip} "
            f"--port {cfg.TARGET_PORT} --concurrency {c} --duration {duration} "
            f"2>/dev/null\"")
        raw = (getattr(out, 'stdout', b'') or b'').decode('utf8', 'ignore').strip()
        try:
            stats = json.loads(raw.splitlines()[-1])
        except (ValueError, IndexError):
            printTerm(f"[benchmark] ... level {c} produced no parseable result; skipping")
            continue
        ok = healthy(stats, slo_s, slo_p)
        printTerm(f"[benchmark] ... success={stats['success_rate']:.3f} "
                  f"p95={stats['p95_ms']:.0f}ms rps={stats['rps']:.0f} "
                  f"-> {'OK' if ok else 'SLO breach'}")
        sweep.append(stats)
        consecutive_bad = 0 if ok else consecutive_bad + 1
        if consecutive_bad >= stop_after:
            printTerm(f"[benchmark] {consecutive_bad} consecutive SLO breaches; stopping sweep")
            break
    return sweep


# --- Teardown -------------------------------------------------------------
def teardown():
    printTerm("[benchmark] Tearing down")
    for _, node in nodes.items():
        try:
            node.delete()
        except Exception:
            pass


def _sig(sig, frame):
    teardown()
    sys.exit(0)


def main():
    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)
    subprocess.run("mkdir flows 2>/dev/null", shell=True)

    try:
        createBridge('br0')
        createController('c1', 'br0')

        web_ip = cfg.web_ip(1)
        printTerm(f"[benchmark] Creating web replica w1 ({web_ip})")
        nodes['w1'] = Host('w1')
        nodes['w1'].instantiate(cfg.webserver)
        attachHost(nodes['w1'], web_ip)

        client_ip = cfg.client_ip(1)
        printTerm(f"[benchmark] Creating client b1 ({client_ip})")
        nodes['b1'] = Host('b1')
        nodes['b1'].instantiate(cfg.linuxclient)
        attachHost(nodes['b1'], client_ip)
        nodes['b1'].copyLocalToContainer('bench_load.py', '/home/bench_load.py')

        # Wait for Apache to serve before measuring.
        url = f"http://{web_ip}:{cfg.TARGET_PORT}/"
        printTerm(f"[benchmark] Waiting for {url} ...")
        healthy_replica = False
        for _ in range(getattr(cfg, 'HEALTHCHECK_RETRIES', 12)):
            r = subprocess.run(f"docker exec b1 wget -q -T 4 -O - {url}",
                               shell=True, capture_output=True)
            if r.returncode == 0:
                healthy_replica = True
                break
            time.sleep(getattr(cfg, 'HEALTHCHECK_INTERVAL', 3))
        if not healthy_replica:
            raise Exception(f"replica {url} did not serve; aborting benchmark")

        configured, cfg_lines = read_configured_capacity(nodes['w1'])
        sweep = run_sweep(nodes['b1'], web_ip)
    finally:
        teardown()

    # --- Analysis -------------------------------------------------------
    slo_s = getattr(cfg, 'BENCH_SLO_SUCCESS', 0.99)
    slo_p = getattr(cfg, 'BENCH_SLO_P95_MS', 500)
    knee, first_bad = find_knee(sweep, slo_s, slo_p)
    knee_c = knee['concurrency'] if knee else (configured or 0)
    rec = recommend(knee_c, getattr(cfg, 'SCALE_TARGET_UTIL', 0.75),
                    getattr(cfg, 'SCALE_DOWN_UTIL', 0.25)) if knee_c else {}

    result = {
        'configured_max_workers': configured,
        'configured_source': cfg_lines,
        'slo': {'success': slo_s, 'p95_ms': slo_p},
        'sweep': sweep,
        'knee_concurrency': knee['concurrency'] if knee else None,
        'first_slo_breach': first_bad['concurrency'] if first_bad else None,
        'target_util': getattr(cfg, 'SCALE_TARGET_UTIL', 0.75),
        'down_util': getattr(cfg, 'SCALE_DOWN_UTIL', 0.25),
        'recommended': rec,
    }
    with open('flows/benchmark.json', 'w') as f:
        json.dump(result, f, indent=2)

    _report(result)


def _report(r):
    print("\n[LST2.0][EDoS] ===== Replica capacity benchmark =====")
    cap = r['configured_max_workers']
    print(f"  Configured ceiling : {cap if cap else 'unknown'} "
          f"(Apache MaxRequestWorkers)")
    print(f"  SLO                : success >= {r['slo']['success']:.0%}, "
          f"p95 <= {r['slo']['p95_ms']:.0f} ms")
    print("  Sweep:")
    for s in r['sweep']:
        print(f"    conc {s['concurrency']:>4} | success {s['success_rate']:.3f} | "
              f"p95 {s['p95_ms']:>6.0f} ms | rps {s['rps']:>6.0f}")
    print(f"  Saturation point   : {r['knee_concurrency']} concurrent connections"
          + (f" (first breach at {r['first_slo_breach']})" if r['first_slo_breach'] else ""))
    if r['recommended']:
        print(f"\n  >>> Ground-truthed thresholds "
              f"(capacity x {r['target_util']:.0%} / {r['down_util']:.0%}):")
        print(f"      SCALE_UP_CONN = {r['recommended']['SCALE_UP_CONN']}")
        print(f"      SCALE_DOWN_CONN = {r['recommended']['SCALE_DOWN_CONN']}")
        print("      (copy into edos_config.py)")
    else:
        print("\n  Not enough healthy levels to recommend thresholds; "
              "widen BENCH_LEVELS or relax the SLO.")
    print("  Detailed JSON      : flows/benchmark.json")


if __name__ == '__main__':
    main()
