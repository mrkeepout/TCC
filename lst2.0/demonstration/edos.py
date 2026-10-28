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

# Brief: EDoS (Economic Denial of Sustainability) experiment orchestrator.
# Builds an elastic cloud service (load balancer + autoscaled web pool behind an
# SDN switch), runs benign clients and slowhttptest-driven attackers, and meters
# the cost the attack inflates. Mirrors the structure of cidds.py. Press CTRL+C
# once to tear everything down and emit the flow + cost reports.

import json
import signal
import subprocess
import sys
import time
from glob import glob
from os import cpu_count, getcwd, path

from host import Host
from switch import Switch
from controller import Controller
from cicflowmeter import CICFlowMeter

import edos_config as cfg
import edos_labeling
from edos_autoscaler import Autoscaler

nodes = {}
autoscaler = None
RUN_START = time.time()


def printTerm(string: str) -> None:
    subprocess.run(f"echo \"{string}\"", shell=True)


# --- Topology building blocks --------------------------------------------
def createBridge(name: str) -> None:
    printTerm(f"[LST2.0][EDoS] Creating Switch {name}")
    nodes[name] = Switch(name, getcwd() + '/flows/' + name, '/home/pcap')
    nodes[name].run('mkdir /home/pcap > /dev/null 2>&1')
    nodes[name].instantiate()
    printTerm("[LST2.0][EDoS] ... Instantiating container")
    nodes[name].setIp(cfg.br_ip, cfg.mask)
    printTerm(f"[LST2.0][EDoS] ... Setting IP {cfg.br_ip}")
    nodes[name].connectToInternet(cfg.gateway, cfg.mask)
    printTerm("[LST2.0][EDoS] ... Connecting to Internet through Host")


def createController(name: str, bridgeName: str) -> None:
    printTerm(f"[LST2.0][EDoS] Creating Controller {name}")
    nodes[name] = Controller(name)
    nodes[name].instantiate()
    nodes[name].connect(nodes[bridgeName])
    nodes[name].setIp(cfg.controller_ip, cfg.mask, nodes[bridgeName])
    nodes[name].initController(cfg.controller_ip, cfg.controller_port)
    nodes[bridgeName].setController(cfg.controller_ip, cfg.controller_port)
    printTerm(f"[LST2.0][EDoS] ... Ryu controller up at "
              f"{cfg.controller_ip}:{cfg.controller_port}")


def attachHost(node: Host, ip: str) -> None:
    # Single /24 behind the bridge: connect, address, default gateway (Internet).
    node.connect(nodes['br0'])
    node.setIp(ip, cfg.mask, nodes['br0'])
    node.setDefaultGateway(cfg.gateway, nodes['br0'])


def createLoadBalancer() -> None:
    printTerm(f"[LST2.0][EDoS] Creating Load Balancer lb ({cfg.lb_vip})")
    nodes['lb'] = Host('lb')
    nodes['lb'].instantiate(cfg.linuxclient)
    attachHost(nodes['lb'], cfg.lb_vip)
    nodes['lb'].copyLocalToContainer('loadbalancer/lb.py', '/home/lb.py')


def startLoadBalancer() -> None:
    printTerm("[LST2.0][EDoS] ... Starting round-robin proxy on VIP")
    nodes['lb'].run(
        f"bash -c 'python3 /home/lb.py {cfg.lb_vip} {cfg.TARGET_PORT} "
        f"/home/backends.txt > /home/lb.log 2>&1 &'")


def expandFleet() -> list:
    # Flatten the declarative FLEET ({profile, count}, ...) into one profile name
    # per client container. Fall back to NUM_CLIENTS homogeneous 'browsing'
    # clients if FLEET is empty, preserving the old "just a client count" usage.
    fleet = getattr(cfg, 'FLEET', None)
    if not fleet:
        return ['browsing'] * cfg.NUM_CLIENTS
    profiles = []
    for entry in fleet:
        profiles.extend([entry['profile']] * int(entry.get('count', 1)))
    return profiles


def createClients() -> None:
    profiles = expandFleet()
    seed = getattr(cfg, 'SEED', 0)
    duration = getattr(cfg, 'EXPERIMENT_DURATION', 0)
    for i, profile in enumerate(profiles, start=1):
        name = f"l{i}"
        printTerm(f"[LST2.0][EDoS] Creating benign client {name} (profile={profile})")
        nodes[name] = Host(name)
        nodes[name].instantiate(cfg.linuxclient)
        attachHost(nodes[name], cfg.client_ip(i))
        nodes[name].copyLocalToContainer('client_profiles.py', '/home/client_profiles.py')
        nodes[name].run(
            f"bash -c 'python3 /home/client_profiles.py "
            f"--profile {profile} --target {cfg.TARGET_VIP} "
            f"--port {cfg.TARGET_PORT} --seed {seed} --client-id {i} "
            f"--duration {duration} > /home/client.log 2>&1 &'")


def createAttackers() -> None:
    for i in range(1, cfg.NUM_ATTACKERS + 1):
        name = f"a{i}"
        printTerm(f"[LST2.0][EDoS] Creating attacker {name} (mode={cfg.ATTACK_MODE})")
        nodes[name] = Host(name)
        nodes[name].instantiate(cfg.linuxclient)
        attachHost(nodes[name], cfg.attacker_ip(i))
        nodes[name].copyLocalToContainer('edos_attack/edos_attacker.py',
                                         '/home/edos_attacker.py')
        nodes[name].run(
            f"bash -c 'python3 /home/edos_attacker.py "
            f"--mode {cfg.ATTACK_MODE} --target {cfg.TARGET_VIP} "
            f"--port {cfg.TARGET_PORT} --test {cfg.SLOW_TEST} "
            f"--conns {cfg.SLOW_CONNS} --rate {cfg.SLOW_RATE} "
            f"--interval {cfg.SLOW_INTERVAL} --pulse-on {cfg.PULSE_ON} "
            f"--pulse-off {cfg.PULSE_OFF} --duration {cfg.EXPERIMENT_DURATION} "
            f"--read-interval {cfg.SLOW_READ_INTERVAL} "
            f"--read-win-lo {cfg.SLOW_READ_WIN_LO} "
            f"--read-win-hi {cfg.SLOW_READ_WIN_HI} "
            f"--read-bytes {cfg.SLOW_READ_BYTES} > /home/attacker.log 2>&1 &'")


# --- Pre-flight: health check + run manifest ------------------------------
def healthCheck() -> None:
    # Confirm the VIP actually serves before we start capturing/attacking, so a
    # topology that didn't come up aborts early instead of producing a silently
    # empty run. Drives the LB container itself, which proxies to the pool.
    retries = getattr(cfg, 'HEALTHCHECK_RETRIES', 12)
    interval = getattr(cfg, 'HEALTHCHECK_INTERVAL', 3)
    url = f"http://{cfg.TARGET_VIP}:{cfg.TARGET_PORT}/"
    printTerm(f"[LST2.0][EDoS] Health check: waiting for {url} to serve...")
    for attempt in range(1, retries + 1):
        r = subprocess.run(f"docker exec lb wget -q -T 4 -O - {url}",
                           shell=True, capture_output=True)
        if r.returncode == 0:
            printTerm(f"[LST2.0][EDoS] ... VIP healthy on attempt {attempt}")
            return
        time.sleep(interval)
    raise Exception(
        f"VIP {url} did not serve after {retries} attempts -- aborting "
        f"(topology/load balancer/replica did not come up)")


def writeManifest() -> None:
    # A dataset must be regenerable: record exactly what produced this run (git
    # SHA, seed, effective config, fleet, timing) so a later split-by-run can be
    # reconstructed. Any JSON-serializable public config value is captured.
    def effective_config():
        out = {}
        for k in dir(cfg):
            if k.startswith('_'):
                continue
            v = getattr(cfg, k)
            if callable(v) or type(v).__name__ == 'module':
                continue
            try:
                json.dumps(v)
                out[k] = v
            except TypeError:
                pass
        return out

    sha = subprocess.run("git rev-parse HEAD", shell=True,
                         capture_output=True).stdout.decode('utf8', 'ignore').strip()
    dirty = subprocess.run("git status --porcelain", shell=True,
                           capture_output=True).stdout.decode('utf8', 'ignore').strip()
    manifest = {
        'git_sha': sha or 'unknown',
        'git_dirty': bool(dirty),
        'timestamp_iso': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
        'timestamp_epoch': int(RUN_START),
        'seed': getattr(cfg, 'SEED', 0),
        'duration': getattr(cfg, 'EXPERIMENT_DURATION', 0),
        'fleet_expanded': expandFleet(),
        'config': effective_config(),
    }
    with open(cfg.RUN_MANIFEST, 'w') as f:
        json.dump(manifest, f, indent=2)
    printTerm(f"[LST2.0][EDoS] Run manifest -> {cfg.RUN_MANIFEST} "
              f"(git {(sha or 'unknown')[:8]}{' +dirty' if dirty else ''}, "
              f"seed {getattr(cfg, 'SEED', 0)})")


# --- Teardown / reporting -------------------------------------------------
def unmakeChanges():
    printTerm("[LST2.0][EDoS] Deleting containers")
    if autoscaler is not None:
        autoscaler.delete_replicas()
    for _, node in nodes.items():
        node.delete()


def convertPcap():
    # flows/br0 keeps the pcaps of every previous run; only convert the ones
    # tshark wrote during this run.
    pcaps = [p for p in glob('flows/br0/*.pcap') if path.getmtime(p) >= RUN_START]
    printTerm(f"[LST2.0][EDoS] Converting {len(pcaps)} pcap files with CICFlowMeter")
    if len(pcaps) == 0:
        return
    hostPath = getcwd() + '/flows'
    containerPath = '/home/flows'
    cic = CICFlowMeter('cic', hostPath, containerPath)
    cic.instantiate()
    cic.analyzeMany(['/home/' + pcap for pcap in pcaps], containerPath,
                    getattr(cfg, 'CIC_JOBS', min(4, cpu_count() or 1)))
    cic.delete()

    # Only CICFlowMeter's output (named after the dump*.pcap files), not
    # edos_cost*.csv or a previous final_report.csv.
    csvs = glob('flows/dump*.csv')
    # De-duplicate the proxy's double-count (#1) and label by IP range (#2),
    # per-file so each file's own header locates the Src/Dst/Label columns.
    canonical_header = ''
    all_rows = []
    totals = {'kept': 0, 'dropped_internal': 0, 'attack': 0, 'benign': 0,
              'unknown': 0, 'malformed': 0}
    for c in csvs:
        with open(c, 'r') as f:
            lines = [ln for ln in f.read().splitlines() if ln.strip()]
        if not lines:
            continue
        header, rows, stats = edos_labeling.process_rows(lines[0], lines[1:])
        canonical_header = header
        all_rows.extend(rows)
        for k in totals:
            totals[k] += stats.get(k, 0)
    subprocess.run('rm -f flows/dump*', shell=True)
    with open(hostPath + '/final_report.csv', 'w') as f:
        f.write(canonical_header + '\n' + '\n'.join(all_rows) + '\n')
    printTerm(
        f"[LST2.0][EDoS] Dataset: {totals['kept']} flows kept "
        f"(attack={totals['attack']} benign={totals['benign']} "
        f"unknown={totals['unknown']}), "
        f"{totals['dropped_internal']} internal-hop flows dropped (dedup), "
        f"{totals['malformed']} malformed")


def signal_handler(sig, frame):
    # Ignore repeated stop requests so a second CTRL+C/click doesn't start a second teardown
    for s in (signal.SIGINT, signal.SIGTERM, signal.SIGALRM):
        signal.signal(s, signal.SIG_IGN)
    print('\n[LST2.0][EDoS] CTRL+C received, tearing down...')
    if autoscaler is not None:
        autoscaler.stop()
        print(autoscaler.summary())
    unmakeChanges()
    convertPcap()
    sys.exit(0)


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGALRM, signal_handler)

try:
    printTerm("LST 2.0 EDoS testbed  Copyright (C) 2022\n\n"
              "\tThis program comes with ABSOLUTELY NO WARRANTY.\n\n")
    subprocess.run("mkdir flows 2>/dev/null", shell=True)

    createBridge('br0')
    createController('c1', 'br0')
    createLoadBalancer()

    # Elastic autoscaler: provisions the initial pool and keeps it in sync.
    autoscaler = Autoscaler(nodes['br0'], 'lb', printTerm)
    autoscaler.provision_initial()
    startLoadBalancer()

    # Pre-flight: don't measure anything until the service actually serves, and
    # record how this run was produced (seed, config, git SHA) for reproducibility.
    healthCheck()
    writeManifest()

    createClients()
    createAttackers()

    nodes['br0'].collectFlows(path='home/pcap', sniffAll=True,
                              rotateInterval=getattr(cfg, 'PCAP_ROTATE_SECONDS', 60))
    autoscaler.start()
except Exception as ex:
    unmakeChanges()
    convertPcap()
    raise Exception(str(ex))

_duration = getattr(cfg, 'EXPERIMENT_DURATION', 0)
if _duration > 0:
    printTerm(f'[LST2.0][EDoS] Running for {_duration}s. Press CTRL+C to stop early.')
    signal.alarm(_duration)
else:
    printTerm('[LST2.0][EDoS] Running. Press CTRL+C to finish the experiment.')
signal.pause()
