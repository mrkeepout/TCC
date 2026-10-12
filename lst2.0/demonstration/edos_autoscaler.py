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

# Brief: Elastic autoscaler for the EDoS testbed. Runs on the host in a daemon
# thread. It samples the load signal (active connections on the LB VIP),
# provisions/decommissions web replicas through the LST Node API, keeps the LB
# backend pool in sync, and meters the cost (replica-seconds) that the attack
# inflates -- the economic dimension that makes this EDoS rather than plain DoS.

import subprocess
import threading
import time

from host import Host

import edos_config as cfg


LB_BACKENDS_LOCAL = 'loadbalancer/backends.txt'
LB_BACKENDS_REMOTE = '/home/backends.txt'


class Autoscaler(threading.Thread):
    # Params:
    #   Switch bridge: the switch every replica connects to
    #   String lb_name: container name of the load balancer
    #   callable log: printing function (e.g. printTerm)
    def __init__(self, bridge, lb_name, log):
        super().__init__(daemon=True)
        self._bridge = bridge
        self._lb_name = lb_name
        self._log = log
        self._running = True
        # (index, Host) for every live replica, in provisioning order.
        self._replicas = []
        self._next_index = 0
        self._last_action = 0.0
        # Consecutive samples below the scale-down threshold (debounce).
        self._low_streak = 0
        # Cost / accounting.
        self.cost = 0.0
        self.replica_seconds = 0.0
        self.scale_up_events = 0
        self.scale_down_events = 0

    # --- Replica lifecycle ------------------------------------------------
    def _provision(self):
        self._next_index += 1
        i = self._next_index
        name = f"w{i}"
        ip = cfg.web_ip(i)
        self._log(f"[autoscaler] SCALE UP -> provisioning {name} ({ip})")
        node = Host(name)
        node.instantiate(cfg.webserver)
        node.connect(self._bridge)
        node.setIp(ip, cfg.mask, self._bridge)
        node.setDefaultGateway(cfg.gateway, self._bridge)
        self._replicas.append((i, node))
        self.scale_up_events += 1
        self._sync_backends()

    def _decommission(self):
        if len(self._replicas) <= cfg.MIN_REPLICAS:
            return
        i, node = self._replicas.pop()
        self._log(f"[autoscaler] SCALE DOWN -> decommissioning w{i}")
        # Drop it from the pool first so no new connection is routed to it.
        self._sync_backends()
        node.delete()
        self.scale_down_events += 1

    def _sync_backends(self):
        lines = [f"{cfg.web_ip(i)}:{cfg.TARGET_PORT}" for i, _ in self._replicas]
        with open(LB_BACKENDS_LOCAL, 'w') as f:
            f.write('\n'.join(lines) + '\n')
        subprocess.run(
            f"docker cp {LB_BACKENDS_LOCAL} {self._lb_name}:{LB_BACKENDS_REMOTE}",
            shell=True, capture_output=True)

    def provision_initial(self):
        for _ in range(cfg.MIN_REPLICAS):
            self._provision()

    # --- Load signal ------------------------------------------------------
    def _active_connections(self):
        # Preferred signal: the LB's own count of live client connections, which
        # it publishes to /home/lb_conns. This is ground truth (the LB knows what
        # it is holding), unlike `ss`, which only sees a fragile kernel snapshot
        # that collapses to ~0 right when the attack saturates the backend.
        out = subprocess.run(
            f"docker exec {self._lb_name} cat /home/lb_conns",
            shell=True, capture_output=True).stdout.decode('utf8', 'ignore').strip()
        if out:
            try:
                return int(out.splitlines()[0])
            except ValueError:
                pass
        # Fallback (LB counter not available yet): count established inbound
        # connections on the LB VIP port via `ss`.
        out = subprocess.run(
            f"docker exec {self._lb_name} ss -Hnt state established "
            f"'( sport = :{cfg.TARGET_PORT} )'",
            shell=True, capture_output=True).stdout.decode('utf8', 'ignore')
        return len([ln for ln in out.splitlines() if ln.strip()])

    # --- Decision loop ----------------------------------------------------
    def _decide(self, conns):
        replicas = len(self._replicas)
        per_replica = conns / max(replicas, 1)
        # Debounce the scale-down signal: track how many consecutive samples have
        # been below the threshold, so a single transient dip (e.g. the momentary
        # drop when the backend saturates) can't tear down a replica mid-attack.
        if per_replica < cfg.SCALE_DOWN_CONN:
            self._low_streak += 1
        else:
            self._low_streak = 0
        if time.time() - self._last_action < cfg.COOLDOWN_SECONDS:
            return
        low_ticks = getattr(cfg, 'SCALE_DOWN_TICKS', 3)
        if per_replica > cfg.SCALE_UP_CONN and replicas < cfg.MAX_REPLICAS:
            self._provision()
            self._last_action = time.time()
            self._low_streak = 0
        elif (self._low_streak >= low_ticks
              and replicas > cfg.MIN_REPLICAS):
            self._decommission()
            self._last_action = time.time()
            self._low_streak = 0

    def run(self):
        with open(cfg.COST_CSV, 'w') as f:
            f.write("timestamp,active_replicas,active_conns,cumulative_cost\n")
        while self._running:
            conns = self._active_connections()
            replicas = len(self._replicas)
            # Bill for the interval that just elapsed.
            self.replica_seconds += replicas * cfg.TICK_SECONDS
            self.cost += replicas * cfg.TICK_SECONDS * cfg.COST_PER_REPLICA_SEC
            with open(cfg.COST_CSV, 'a') as f:
                f.write(f"{time.time():.0f},{replicas},{conns},{self.cost:.6f}\n")
            self._log(f"[autoscaler] replicas={replicas} conns={conns} "
                      f"cost={self.cost:.4f}")
            self._decide(conns)
            time.sleep(cfg.TICK_SECONDS)

    # --- Teardown ---------------------------------------------------------
    def stop(self):
        self._running = False

    def delete_replicas(self):
        for _, node in self._replicas:
            node.delete()
        self._replicas = []

    def summary(self):
        return (
            "\n[LST2.0][EDoS] ===== Cost report =====\n"
            f"  Scale-up events   : {self.scale_up_events}\n"
            f"  Scale-down events : {self.scale_down_events}\n"
            f"  Replica-seconds   : {self.replica_seconds:.0f}\n"
            f"  Estimated cost    : {self.cost:.4f} "
            f"(@ {cfg.COST_PER_REPLICA_SEC}/replica-s)\n"
            f"  Detailed timeline : {cfg.COST_CSV}\n"
        )
