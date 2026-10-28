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


# Brief: Central configuration for the EDoS (Economic Denial of Sustainability)
# experiment topology. All tunables live here so the experiment can be reshaped
# without touching the orchestration logic.

# --- Docker images (reuse the pre-built LST 2.0 images, no new build needed) ---
repository = 'alexandremitsurukaihara/lst2.0'
webserver = repository + ':web'          # scalable backend (Apache on :80)
linuxclient = repository + ':linuxclient'  # load balancer, attackers and clients

# --- Network layout: everything lives on a single /24 behind br0 ---
subnet = '192.168.100.'
mask = 24
br_ip = subnet + '100'          # bridge address inside the switch container
gateway = subnet + '101'        # host-side veth: default gateway + Internet exit
controller_ip = subnet + '102'
controller_port = 6653

lb_vip = subnet + '10'          # single virtual IP that clients/attackers hit


def web_ip(i: int) -> str:
    # Web replicas occupy .21 .. .29 (w1 -> .21)
    return subnet + str(20 + i)


def client_ip(i: int) -> str:
    # Legitimate clients occupy .51 .. .59 (l1 -> .51)
    return subnet + str(50 + i)


def attacker_ip(i: int) -> str:
    # Attackers occupy .71 .. .79 (a1 -> .71)
    return subnet + str(70 + i)


# --- Reproducibility ---
# Single seed for the whole run, propagated to the benign clients (and later the
# attack) so a run can be regenerated. Each client mixes this with its id, so the
# fleet is deterministic yet its members are not identical.
SEED = 1337

# --- Benign fleet (configurable: how many, and which type) ---
# Declarative fleet: each entry picks a client PROFILE and how many of it to run.
# createClients() expands this into individual client containers on .51..59 and
# launches client_profiles.py with the chosen profile. Grow the list without
# touching the orchestration code. Profiles: browsing | mailing | printing
# (the LST 2.0 behaviours reimplemented over HTTP in client_profiles.py).
FLEET = [
    {'profile': 'browsing', 'count': 2},   # SURGE: human-like page views
    {'profile': 'mailing',  'count': 1},    # inbox fetch bursts + send w/ attachment
    {'profile': 'printing', 'count': 1},    # occasional large document uploads
]

# Fallback: if FLEET is empty, fall back to NUM_CLIENTS homogeneous 'browsing'
# clients so the old behaviour (a plain client count) still works.
NUM_CLIENTS = 2
NUM_ATTACKERS = 2

# --- Autoscaler (elasticity + billing) ---
MIN_REPLICAS = 1
MAX_REPLICAS = 3
# Load signal = active connections on the LB VIP, normalized per active replica.
# GROUND TRUTH: these thresholds should come from a replica's MEASURED capacity,
# not a guess. Run `sudo python3 edos_benchmark.py` to sweep a single replica's
# concurrency, find its saturation knee, and print SCALE_UP_CONN/SCALE_DOWN_CONN
# as a target-utilisation fraction of that knee (see SCALE_TARGET_UTIL below).
# The defaults here are a placeholder until the benchmark is run on your machine.
SCALE_UP_CONN = 50      # if conns/replica exceeds this -> scale up
SCALE_DOWN_CONN = 10    # if conns/replica drops below this -> scale down
TICK_SECONDS = 5        # autoscaler sampling/decision interval
COOLDOWN_SECONDS = 10   # minimum time between two scaling actions
# Scale-down debounce: require this many consecutive low samples before removing a
# replica, so a single transient dip in the load signal can't tear down capacity
# in the middle of a sustained attack (which caused up/down flapping).
SCALE_DOWN_TICKS = 3
COST_PER_REPLICA_SEC = 0.0001  # legacy constant, kept for reference only; the
#                                real cost now comes from PRICING below.

# --- Autoscaler realism (Phase 2) -------------------------------------------
# A provisioned replica does not serve instantly: it is billed (you pay for the
# boot) but stays OUT of the LB pool for WARMUP_SECONDS. This is exactly the
# window a yo-yo attack exploits -- capacity paid for but not yet useful.
WARMUP_SECONDS = 30
# On scale-down a replica is first removed from the pool (no new connections),
# then kept alive for DRAIN_TIMEOUT_SECONDS so in-flight legitimate connections
# finish instead of being cut (maps to an ALB target deregistration delay). It
# is still billed while draining.
DRAIN_TIMEOUT_SECONDS = 30

# --- Cost model: pluggable pricing profile (AWS reference) ------------------
# Replaces the single arbitrary COST_PER_REPLICA_SEC with a documented, swappable
# profile. Total cost over the run is
#
#   C = Σ_k p_vm · R_billed(t_k) · Δt                          (compute)
#     + Σ_k p_lcu · max(C_conn/conn_dim, R_new/newconn_dim) · Δt  (ALB LCU)
#     + p_bw · Σ_k B(t_k)                                        (data processed)
#
# where R_billed counts every ALIVE replica (warming + active + draining) and a
# replica that lives less than billing_quantum_sec is still charged the full
# quantum -- which is what makes a yo-yo (rapid up/down) economically distinct
# from a sustained attack.
#
# Three cloud profiles are provided (AWS / Azure / GCP), each number cited. The
# GUI lets you pick one (loads its defaults) and still edit any value. The active
# profile is PRICING; PRICING_CLOUD names which one it came from.
#
# Modelling notes (what maps cleanly and what doesn't, per cloud):
#  * The connection-based LB unit (p_lcu + conn dims) models AWS ALB "LCU" and
#    Azure Application Gateway v2 "Capacity Unit" directly. GCP Cloud Load
#    Balancing has NO connection-based unit (it bills forwarding rules + data
#    processed), so for GCP p_lcu_per_sec = 0 and the LB cost rides on p_bw_per_gb.
#  * The fixed hourly LB charge (ALB ~$0.0225/h, App Gateway ~$0.246/h, GCP fwd
#    rule ~$0.025/h) is a constant paid with or without attack, so it cancels out
#    of the EDoS overhead (NCO/EAF) and is intentionally omitted.
#  * billing_quantum_sec is the compute minimum-billing increment: AWS EC2 and
#    GCE bill per-second with a 60s minimum; Azure VMs bill per-second with no
#    such minimum (quantum ~1s), which notably softens the yo-yo compute penalty.
PRICING_PROFILES = {
    'aws': {
        'cloud': 'AWS us-east-1',
        'p_vm_per_sec': 0.0416 / 3600,   # EC2 t3.medium on-demand, $0.0416/h
        'p_lcu_per_sec': 0.008 / 3600,   # ALB, $0.008 per LCU-hour
        'lcu_active_conn_dim': 3000.0,   # ALB LCU: 3000 active connections = 1 LCU
        'lcu_new_conn_dim': 25.0,        # ALB LCU: 25 new connections/s   = 1 LCU
        'p_bw_per_gb': 0.09,             # EC2 data transfer out, ~$0.09/GB
        'billing_quantum_sec': 60.0,     # EC2 per-second billing, 60s minimum
        'sources': {
            'p_vm_per_sec': 'AWS EC2 t3.medium on-demand us-east-1 ($0.0416/h)',
            'p_lcu_per_sec': 'AWS ALB pricing ($0.008 per LCU-hour)',
            'lcu_dims': 'AWS ALB LCU: 25 new conn/s, 3000 active conn, 1 GB/h',
            'p_bw_per_gb': 'AWS EC2 data transfer out (~$0.09/GB)',
            'billing_quantum_sec': 'AWS EC2 per-second billing, 60s minimum',
        },
    },
    'azure': {
        'cloud': 'Azure East US',
        'p_vm_per_sec': 0.0416 / 3600,   # VM B2s (2 vCPU/4GB) Linux, $0.0416/h
        'p_lcu_per_sec': 0.008 / 3600,   # App Gateway Standard_V2, $0.008/CU-hour
        'lcu_active_conn_dim': 2500.0,   # App Gateway CU: 2500 persistent connections
        'lcu_new_conn_dim': 50.0,        # Standard_V2: 50 new conn/s per compute unit
        'p_bw_per_gb': 0.087,            # Azure egress, ~$0.087/GB
        'billing_quantum_sec': 1.0,      # Azure VM per-second billing, no 60s minimum
        'sources': {
            'p_vm_per_sec': 'Azure VM Standard_B2s Linux East US (~$0.0416/h)',
            'p_lcu_per_sec': 'Azure Application Gateway Standard_V2 ($0.008/CU-hour)',
            'lcu_dims': 'App Gateway v2 CU: 2500 persistent conn, 50 new conn/s, 2.22 Mbps',
            'p_bw_per_gb': 'Azure bandwidth egress (~$0.087/GB)',
            'billing_quantum_sec': 'Azure VM per-second billing (no 60s minimum)',
            'note': 'Fixed gateway charge (~$0.246/h) omitted as an attack-independent constant',
        },
    },
    'gcp': {
        'cloud': 'GCP us-central1',
        'p_vm_per_sec': 0.0335 / 3600,   # e2-medium on-demand, ~$0.0335/h
        'p_lcu_per_sec': 0.0,            # GCP LB has NO connection-based unit
        'lcu_active_conn_dim': 3000.0,   # inert while p_lcu_per_sec = 0 (placeholder)
        'lcu_new_conn_dim': 25.0,        # inert while p_lcu_per_sec = 0 (placeholder)
        'p_bw_per_gb': 0.12,             # GCP internet egress (~$0.12/GB)
        'billing_quantum_sec': 60.0,     # GCE per-second billing, 1-minute minimum
        'sources': {
            'p_vm_per_sec': 'GCP Compute Engine e2-medium us-central1 (~$0.0335/h)',
            'p_lcu_per_sec': 'GCP Cloud LB has no connection unit -> 0 (bills data processed)',
            'lcu_dims': 'N/A for GCP (LB priced by forwarding rules + data processed $0.008/GiB)',
            'p_bw_per_gb': 'GCP internet egress (~$0.12/GB)',
            'billing_quantum_sec': 'GCE per-second billing, 1-minute minimum',
            'note': 'Forwarding-rule charge (~$0.025/h) omitted as an attack-independent constant',
        },
    },
}

# Active profile (the GUI can switch this and edit any value).
PRICING_CLOUD = 'aws'
import copy as _copy
PRICING = _copy.deepcopy(PRICING_PROFILES[PRICING_CLOUD])
del _copy

# --- Attack model (slowhttptest driven) ---
# ATTACK_MODE: "sustained" keeps the service pinned at MAX; "yoyo" pulses the
# load to force repeated scale-up/scale-down cycles (classic EDoS).
ATTACK_MODE = 'yoyo'  # sustained | yoyo
SLOW_TEST = 'slowloris'   # slowloris | slowpost | slowread
SLOW_CONNS = 200          # slowhttptest -c : concurrent connections per attacker
SLOW_RATE = 50            # slowhttptest -r : connections opened per second
SLOW_INTERVAL = 3        # slowhttptest -i : seconds between follow-up data
# yoyo pulse timing MUST respect the replica lifecycle, or the service never
# actually scales back down between pulses and the "yoyo" degenerates into a
# "sustained" run (capacity only grows). Rules of thumb for the current autoscaler:
#   PULSE_ON  >= WARMUP_SECONDS                          (new replicas warm & serve)
#   PULSE_OFF >= SCALE_DOWN_TICKS*TICK_SECONDS + DRAIN_TIMEOUT_SECONDS
#                                                        (scale-down debounces AND drains)
# With WARMUP=30, DRAIN=30, SCALE_DOWN_TICKS=3, TICK=5 -> PULSE_OFF >= ~45s.
PULSE_ON = 60             # yoyo: seconds attacking   (>= WARMUP so capacity serves)
PULSE_OFF = 60            # yoyo: seconds idle        (>= debounce+DRAIN so it scales down)

# Slow Read (-X) shaping: a small advertised window + slow read cadence keeps the
# server-side connections established for the whole run (otherwise slowread
# finishes reading quickly and the connections close). Ignored by other tests.
SLOW_READ_INTERVAL = 5    # slowhttptest -n : seconds between read operations
SLOW_READ_WIN_LO = 512    # slowhttptest -w : lower bound of advertised window (bytes)
SLOW_READ_WIN_HI = 1024   # slowhttptest -y : upper bound of advertised window (bytes)
SLOW_READ_BYTES = 32      # slowhttptest -z : bytes read from the buffer per read()

TARGET_VIP = lb_vip
TARGET_PORT = 80

# --- Capture / dataset ---
# tshark rotates the pcap every PCAP_ROTATE_SECONDS. A larger value cuts fewer
# long slow connections at a rotation boundary (fewer SYN-less halves for
# CICFlowMeter to mis-direction), complementing the flip-robust labelling.
PCAP_ROTATE_SECONDS = 300

# --- Health check (abort early instead of capturing an empty run) ---
HEALTHCHECK_RETRIES = 12
HEALTHCHECK_INTERVAL = 3   # seconds between attempts

# --- Capacity benchmark (edos_benchmark.py) ---
# Sweeps simultaneous-connection levels against ONE replica to find its
# saturation knee, giving the autoscaler thresholds a measured basis.
BENCH_LEVELS = [10, 25, 50, 75, 100, 125, 150, 200, 250, 300]
BENCH_DURATION = 8           # seconds of load per level
BENCH_SLO_SUCCESS = 0.99     # min success rate for a level to count as "healthy"
BENCH_SLO_P95_MS = 500       # max p95 latency (ms) for a level to count as "healthy"
BENCH_STOP_AFTER_FAILS = 2   # stop the sweep after this many consecutive failing levels
# Derive thresholds from the measured knee: scale up at TARGET_UTIL of capacity,
# scale down below DOWN_UTIL (hysteresis). These replace the arbitrary defaults.
SCALE_TARGET_UTIL = 0.75
SCALE_DOWN_UTIL = 0.25

# --- Output ---
COST_CSV = 'flows/edos_cost.csv'
RUN_MANIFEST = 'flows/run_manifest.json'

# --- Experiment duration (0 = unlimited, stop with CTRL+C) ---
# Default spans 3 full yoyo cycles (3 x (PULSE_ON+PULSE_OFF) = 3 x 120s), so a
# default run actually shows repeated scale-up/scale-down instead of a single ramp.
EXPERIMENT_DURATION = 360

# --- Runtime overrides (used by edos_gui.py) ---
# If the GUI is running, it writes a temporary .edos_overrides.py with only the
# parameters the user changed. This block applies those overrides at import time.
# When running edos.py directly (no GUI), the file does not exist and defaults
# above are used unchanged.
import os as _os
_override_path = _os.path.join(
    _os.path.dirname(_os.path.abspath(__file__)), '.edos_overrides.py')
if _os.path.isfile(_override_path):
    with open(_override_path) as _f:
        exec(compile(_f.read(), _override_path, 'exec'))
    del _f
del _override_path, _os
