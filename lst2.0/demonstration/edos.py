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
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from glob import glob
from os import cpu_count, getcwd, path
from pathlib import Path
from urllib.parse import quote

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
PYDASH_CLIENTS = set()


def printTerm(string: str) -> None:
    subprocess.run(f"echo \"{string}\"", shell=True)


def archivePreviousNewArtifacts() -> None:
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    previous_hrf = Path(cfg.HRF_EVENTS_PATH)
    if previous_hrf.is_file() and previous_hrf.stat().st_size:
        previous_hrf.rename(
            previous_hrf.with_name(f"hrf_events_{timestamp}.jsonl"))
    for previous in Path("flows").glob("pydash_l*"):
        if previous.is_dir():
            previous.rename(
                previous.with_name(f"archived_{previous.name}_{timestamp}"))


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
    subprocess.run(
        ["docker", "cp", "loadbalancer/lb.py", "lb:/home/lb.py"],
        check=True, capture_output=True, text=True)
    subprocess.run(
        ["docker", "cp", "http_request_filtering.py",
         "lb:/home/http_request_filtering.py"],
        check=True, capture_output=True, text=True)


def startLoadBalancer() -> None:
    printTerm("[LST2.0][EDoS] ... Starting round-robin proxy on VIP")
    args = [
        "python3", "/home/lb.py", cfg.lb_vip, str(cfg.TARGET_PORT),
        "/home/backends.txt",
    ]
    if getattr(cfg, 'HRF_ENABLED', False):
        args.extend([
            "--hrf-log", "/home/hrf_events.jsonl",
            "--hrf-size-bytes", str(cfg.HRF_SIZE_THRESHOLD_BYTES),
            "--hrf-requests-per-second",
            str(cfg.HRF_REQUESTS_PER_SECOND_THRESHOLD),
            "--hrf-window-seconds", str(cfg.HRF_RATE_WINDOW_SECONDS),
            "--hrf-allowlist", ",".join(cfg.HRF_ALLOWLIST),
            "--hrf-blocklist", ",".join(cfg.HRF_BLOCKLIST),
        ])
    command = shlex.join(args) + " > /home/lb.log 2>&1 &"
    result = nodes['lb'].run("bash -c " + shlex.quote(command))
    if result.returncode:
        raise RuntimeError(
            "Could not start load balancer: "
            + result.stderr.decode("utf-8", "replace"))


def expandFleet() -> list:
    # Flatten the declarative FLEET ({profile, count}, ...) into one profile name
    # per client container. Fall back to NUM_CLIENTS homogeneous 'browsing'
    # clients if FLEET is empty, preserving the old "just a client count" usage.
    fleet = getattr(cfg, 'FLEET', None)
    if not fleet:
        return ['browsing'] * cfg.NUM_CLIENTS
    profiles = []
    for entry in fleet:
        profile = entry['profile']
        if profile == 'pydash' and not getattr(cfg, 'PYDASH_ENABLED', False):
            continue
        profiles.extend([profile] * int(entry.get('count', 1)))
    return profiles


def preparePydash() -> None:
    if not getattr(cfg, 'PYDASH_ENABLED', False):
        return
    if cfg.TARGET_PORT != 80:
        raise ValueError(
            "The local pyDash ConnectionHandler uses HTTP port 80; "
            "set TARGET_PORT=80 for the pyDash profile")
    source = Path(cfg.PYDASH_SOURCE_DIR).expanduser().resolve()
    media = Path(cfg.PYDASH_MEDIA_DIR).expanduser().resolve()
    mpd = Path(cfg.PYDASH_MPD_FILE)
    if not (source / "main.py").is_file() or not (source / "dash_client.json").is_file():
        raise FileNotFoundError(
            f"PYDASH_SOURCE_DIR must contain main.py and dash_client.json: {source}")
    if not media.is_dir():
        raise FileNotFoundError(
            f"Set PYDASH_MEDIA_DIR to a local DASH asset directory: {media}")
    if mpd.is_absolute() or ".." in mpd.parts or not (media / mpd).is_file():
        raise FileNotFoundError(
            f"PYDASH_MPD_FILE must name an MPD file inside {media}: {mpd}")
    media_url_path = Path(cfg.PYDASH_MEDIA_URL_PATH)
    if media_url_path.is_absolute() or ".." in media_url_path.parts:
        raise ValueError("PYDASH_MEDIA_URL_PATH must be a relative URL path")

    check = subprocess.run(
        ["docker", "image", "inspect", cfg.pydashclient],
        capture_output=True, text=True)
    if check.returncode == 0:
        return
    if not getattr(cfg, 'PYDASH_BUILD_IMAGE', False):
        raise RuntimeError(
            f"Docker image {cfg.pydashclient} is missing; build "
            "lst2.0/docker/pydash_client or enable PYDASH_BUILD_IMAGE")
    context = Path(__file__).resolve().parent.parent / "docker" / "pydash_client"
    printTerm(f"[LST2.0][EDoS] Building pyDash image {cfg.pydashclient}")
    subprocess.run(
        ["docker", "build", "--tag", cfg.pydashclient, str(context)],
        check=True)


def createPydashClient(node: Host, client_id: int) -> None:
    source = Path(cfg.PYDASH_SOURCE_DIR).expanduser().resolve()
    media_url_path = cfg.PYDASH_MEDIA_URL_PATH.strip("/")
    mpd_path = quote(Path(cfg.PYDASH_MPD_FILE).as_posix(), safe="/")
    mpd_url = f"http://{cfg.TARGET_VIP}/{media_url_path}/{mpd_path}"

    with open(source / "dash_client.json", encoding="utf-8") as config_file:
        dash_config = json.load(config_file)
    dash_config["url_mpd"] = mpd_url

    with tempfile.TemporaryDirectory(prefix="lst-pydash-") as staging:
        staged = Path(staging) / "pydash"
        shutil.copytree(
            source, staged,
            ignore=shutil.ignore_patterns(
                ".git", "venv", "__pycache__", "*.pyc", "results"))
        player_path = staged / "player" / "player.py"
        player_source = player_path.read_text(encoding="utf-8")
        original_path = "segment_request.add_path_name('/'.join(url_tokens[:len(url_tokens) - 1]))"
        local_path = "segment_request.add_path_name('/' + '/'.join(url_tokens[3:-1]))"
        if player_source.count(original_path) != 1:
            raise RuntimeError(
                "Unsupported pyDash Player.request_next_segment implementation; "
                "cannot safely make its segment URLs relative")
        player_path.write_text(
            player_source.replace(original_path, local_path, 1),
            encoding="utf-8")
        (staged / "dash_client.json").write_text(
            json.dumps(dash_config, indent=2) + "\n", encoding="utf-8")
        subprocess.run(
            ["docker", "cp", str(staged) + "/.", f"{node.getNodeName()}:/home/pydash"],
            check=True, capture_output=True, text=True)

    subprocess.run(
        ["docker", "cp", "pydash_runner.py",
         f"{node.getNodeName()}:/home/pydash_runner.py"],
        check=True, capture_output=True, text=True)
    subprocess.run(
        ["docker", "exec", node.getNodeName(), "mkdir", "-p",
         "/home/pydash/results"],
        check=True, capture_output=True, text=True)

    command = shlex.join([
        "python3", "/home/pydash_runner.py",
        "--source", "/home/pydash",
        "--log", "/home/pydash.log",
        "--events", "/home/pydash_events.csv",
        "--summary", "/home/pydash_qoe.json",
    ]) + " > /home/pydash_runner.log 2>&1 &"
    result = node.run("bash -c " + shlex.quote(command))
    if result.returncode:
        raise RuntimeError(
            f"Could not start pyDash client {node.getNodeName()}: "
            + result.stderr.decode("utf-8", "replace"))
    PYDASH_CLIENTS.add(node.getNodeName())


def createClients() -> None:
    profiles = expandFleet()
    seed = getattr(cfg, 'SEED', 0)
    duration = getattr(cfg, 'EXPERIMENT_DURATION', 0)
    for i, profile in enumerate(profiles, start=1):
        name = f"l{i}"
        printTerm(f"[LST2.0][EDoS] Creating benign client {name} (profile={profile})")
        nodes[name] = Host(name)
        image = cfg.pydashclient if profile == 'pydash' else cfg.linuxclient
        nodes[name].instantiate(image)
        attachHost(nodes[name], cfg.client_ip(i))
        if profile == 'pydash':
            createPydashClient(nodes[name], i)
        else:
            nodes[name].copyLocalToContainer(
                'client_profiles.py', '/home/client_profiles.py')
            nodes[name].run(
                f"bash -c 'python3 /home/client_profiles.py "
                f"--profile {profile} --target {cfg.TARGET_VIP} "
                f"--port {cfg.TARGET_PORT} --seed {seed} --client-id {i} "
                f"--duration {duration} --qos-csv /home/client_qos.csv "
                f"> /home/client.log 2>&1 &'")


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


def checkPydashMedia() -> None:
    if not getattr(cfg, 'PYDASH_ENABLED', False):
        return
    media_url_path = cfg.PYDASH_MEDIA_URL_PATH.strip("/")
    mpd_path = quote(Path(cfg.PYDASH_MPD_FILE).as_posix(), safe="/")
    url = (f"http://{cfg.TARGET_VIP}:{cfg.TARGET_PORT}/"
           f"{media_url_path}/{mpd_path}")
    result = subprocess.run(
        ["docker", "exec", "lb", "wget", "-q", "-T", "8", "-O", "/dev/null", url],
        capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(
            f"pyDash MPD is not reachable through the VIP ({url}): "
            f"{result.stderr.strip()}")


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
def collectClientQoS():
    # Pull each benign client's per-request QoS log OUT of its container before
    # the container is destroyed -- otherwise the data (latency/success of the
    # legitimate user, i.e. the attack's collateral damage) dies with it.
    for name, node in nodes.items():
        if not name.startswith('l'):
            continue
        if name in PYDASH_CLIENTS:
            destination = Path('flows') / f'pydash_{name}'
            destination.mkdir(parents=True, exist_ok=True)
            for remote_name in (
                "pydash.log", "pydash_runner.log",
                "pydash_events.csv", "pydash_qoe.json",
            ):
                result = subprocess.run(
                    ["docker", "cp", f"{name}:/home/{remote_name}",
                     str(destination / remote_name)],
                    capture_output=True, text=True)
                if result.returncode:
                    printTerm(
                        f"[LST2.0][EDoS] WARNING: no {remote_name} from {name}: "
                        f"{result.stderr.strip()}")
            results_dir = destination / "results"
            results_dir.mkdir(exist_ok=True)
            result = subprocess.run(
                ["docker", "cp", f"{name}:/home/pydash/results/.",
                 str(results_dir)],
                capture_output=True, text=True)
            if result.returncode:
                printTerm(
                    f"[LST2.0][EDoS] WARNING: pyDash plots unavailable for "
                    f"{name}: {result.stderr.strip()}")
            continue
        try:
            node.copyContainerToLocal('/home/client_qos.csv',
                                      f'flows/qos_{name}.csv')
        except Exception:
            pass  # a client that never wrote a QoS file just has nothing to copy


def collectHRFEvents() -> None:
    if not getattr(cfg, 'HRF_ENABLED', False) or 'lb' not in nodes:
        return
    destination = Path(cfg.HRF_EVENTS_PATH)
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["docker", "cp", "lb:/home/hrf_events.jsonl", str(destination)],
        capture_output=True, text=True)
    if result.returncode:
        printTerm(
            "[LST2.0][EDoS] WARNING: HRF event log unavailable: "
            + result.stderr.strip())


def unmakeChanges():
    collectHRFEvents()
    collectClientQoS()
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
    checkCaptureCoverage(canonical_header, all_rows)
    printTerm(
        f"[LST2.0][EDoS] Dataset: {totals['kept']} flows kept "
        f"(attack={totals['attack']} benign={totals['benign']} "
        f"unknown={totals['unknown']}), "
        f"{totals['dropped_internal']} internal-hop flows dropped (dedup), "
        f"{totals['malformed']} malformed")


def checkCaptureCoverage(header, rows, min_fraction=0.95):
    # Sanity check: the flows must span (almost) the whole run. A capture that
    # dies mid-run (e.g. tshark losing one of its sniffed interfaces) used to
    # go unnoticed and silently truncate the dataset.
    from datetime import datetime
    duration = getattr(cfg, 'EXPERIMENT_DURATION', 0)
    try:
        idx = [c.strip() for c in header.split(',')].index('Timestamp')
        ts = []
        for row in rows:
            try:
                ts.append(datetime.strptime(row.split(',')[idx].strip(),
                                            '%d/%m/%Y %I:%M:%S %p'))
            except (ValueError, IndexError):
                pass
        span = (max(ts) - min(ts)).total_seconds() if ts else 0
    except ValueError:
        printTerm("[LST2.0][EDoS] WARNING: could not check capture coverage")
        return
    if duration > 0 and span < min_fraction * duration:
        printTerm(f"[LST2.0][EDoS] WARNING: CAPTURE TRUNCATED -- flows span only "
                  f"{span:.0f}s of a {duration}s run. Do NOT use this run.")
    else:
        printTerm(f"[LST2.0][EDoS] Capture coverage OK: flows span {span:.0f}s")


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
    archivePreviousNewArtifacts()
    preparePydash()
    if getattr(cfg, 'HRF_ENABLED', False):
        Path(cfg.HRF_EVENTS_PATH).parent.mkdir(parents=True, exist_ok=True)
        Path(cfg.HRF_EVENTS_PATH).write_text('', encoding='utf-8')

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
    checkPydashMedia()
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
