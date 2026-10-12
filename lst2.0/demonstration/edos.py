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


def createClients() -> None:
    for i in range(1, cfg.NUM_CLIENTS + 1):
        name = f"l{i}"
        printTerm(f"[LST2.0][EDoS] Creating benign client {name}")
        nodes[name] = Host(name)
        nodes[name].instantiate(cfg.linuxclient)
        attachHost(nodes[name], cfg.client_ip(i))
        nodes[name].copyLocalToContainer('legit_client.py', '/home/legit_client.py')
        nodes[name].run(
            f"bash -c 'python3 /home/legit_client.py {cfg.TARGET_VIP} "
            f"{cfg.TARGET_PORT} > /home/client.log 2>&1 &'")


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
    contents = []
    for c in csvs:
        with open(c, 'r') as f:
            contents.append(f.read())
    merged = '\n'.join(contents).split('\n')
    header = merged[0] if merged else ''
    body = [ln for ln in merged if 'Flow ID' not in ln]
    subprocess.run('rm -f flows/dump*', shell=True)
    with open(hostPath + '/final_report.csv', 'w') as f:
        f.write(header + '\n' + '\n'.join(body))


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

    createClients()
    createAttackers()

    nodes['br0'].collectFlows(path='home/pcap', sniffAll=True)
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
