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
# from a sustained attack. Every number cites its source so the model is
# defensible and can be swapped for GCP/Azure.
PRICING = {
    'cloud': 'AWS us-east-1 (reference)',
    'p_vm_per_sec': 0.0416 / 3600,   # EC2 t3.medium on-demand, $0.0416/h
    'p_lcu_per_sec': 0.008 / 3600,   # ALB, $0.008 per LCU-hour
    'lcu_active_conn_dim': 3000.0,   # ALB LCU: 3000 active connections = 1 LCU
    'lcu_new_conn_dim': 25.0,        # ALB LCU: 25 new connections/s   = 1 LCU
    'p_bw_per_gb': 0.09,             # data transfer out, ~$0.09/GB
    'billing_quantum_sec': 60.0,     # EC2 per-second billing has a 60s minimum
    'sources': {
        'p_vm_per_sec': 'AWS EC2 t3.medium on-demand us-east-1 ($0.0416/h)',
        'p_lcu_per_sec': 'AWS ALB pricing ($0.008 per LCU-hour)',
        'lcu_dims': 'AWS ALB LCU dimensions: 25 new conn/s, 3000 active conn, 1 GB/h',
        'p_bw_per_gb': 'AWS EC2 data transfer out (~$0.09/GB)',
        'billing_quantum_sec': 'AWS EC2 per-second billing, 60s minimum charge',
    },
}

# --- Attack model (slowhttptest driven) ---
# ATTACK_MODE: "sustained" keeps the service pinned at MAX; "yoyo" pulses the
# load to force repeated scale-up/scale-down cycles (classic EDoS).
ATTACK_MODE = 'yoyo'  # sustained | yoyo
SLOW_TEST = 'slowloris'   # slowloris | slowpost | slowread
SLOW_CONNS = 200          # slowhttptest -c : concurrent connections per attacker
SLOW_RATE = 50            # slowhttptest -r : connections opened per second
SLOW_INTERVAL = 3        # slowhttptest -i : seconds between follow-up data
PULSE_ON = 20             # yoyo: seconds attacking
PULSE_OFF = 20            # yoyo: seconds idle (let it scale down)

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

# --- Output ---
COST_CSV = 'flows/edos_cost.csv'
RUN_MANIFEST = 'flows/run_manifest.json'

# --- Experiment duration (0 = unlimited, stop with CTRL+C) ---
EXPERIMENT_DURATION = 0

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
