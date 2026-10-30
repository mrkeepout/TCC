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
# backend pool in sync, and meters the cost the attack inflates.
#
# Phase 2 makes the elasticity (and therefore the cost) realistic -- which is
# load-bearing for the yo-yo contribution, not polish:
#   * WARMUP   -- a new replica is billed but out of the pool until it warms up,
#                 the window a yo-yo exploits (pay for boot, get no service).
#   * DRAINING -- on scale-down a replica leaves the pool, then finishes its
#                 in-flight connections before being removed (no cut clients).
#   * QUANTUM  -- a replica that lives less than the billing quantum is still
#                 charged the full quantum, so rapid up/down of a yo-yo pays for
#                 capacity it barely used.
#   * LCU cost -- compute (per billed replica) + ALB LCU (active & new conns) +
#                 data processed, from a documented, swappable pricing profile.

import subprocess
import threading
import time

from host import Host

import edos_config as cfg


LB_BACKENDS_LOCAL = 'loadbalancer/backends.txt'
LB_BACKENDS_REMOTE = '/home/backends.txt'

# Replica lifecycle states.
WARMING = 'warming'    # billed, NOT in the pool yet
ACTIVE = 'active'      # in the pool, serving
DRAINING = 'draining'  # billed, out of the pool, finishing in-flight connections


class Replica:
    def __init__(self, index, node, now):
        self.index = index
        self.node = node
        self.state = WARMING
        self.start = now         # billing start (provisioning)
        self.state_since = now   # when it entered the current state
        self.end = None          # set when fully removed


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
        self._replicas = []        # all live replicas (any state), provisioning order
        self._next_index = 0
        self._last_action = 0.0
        self._low_streak = 0       # consecutive low samples (scale-down debounce)
        # Rate baselines for the LCU model.
        self._prev_time = 0.0
        self._prev_accepted = 0
        self._prev_bytes = 0
        # Cost / accounting.
        self._price = getattr(cfg, 'PRICING', {})
        self.vm_cost = 0.0
        self.lcu_cost = 0.0
        self.bw_cost = 0.0
        self.cost = 0.0
        self.replica_seconds = 0.0
        self.quantum_penalty = 0.0   # extra charged by the billing quantum
        self.scale_up_events = 0
        self.scale_down_events = 0

    # --- Replica lifecycle ------------------------------------------------
    def _provision(self, immediate=False):
        self._next_index += 1
        i = self._next_index
        name = f"w{i}"
        ip = cfg.web_ip(i)
        now = time.time()
        node = Host(name)
        node.instantiate(cfg.webserver)
        node.connect(self._bridge)
        node.setIp(ip, cfg.mask, self._bridge)
        node.setDefaultGateway(cfg.gateway, self._bridge)
        r = Replica(i, node, now)
        if immediate:
            # Baseline pool: the service was already warm before the attack.
            r.state = ACTIVE
            self._log(f"[autoscaler] START -> {name} ({ip}) active")
        else:
            self._log(f"[autoscaler] SCALE UP -> {name} ({ip}) warming "
                      f"({self._warmup()}s before it serves)")
        self._replicas.append(r)
        self.scale_up_events += 1
        self._sync_backends()

    def _decommission(self):
        # Drain the NEWEST active replica (LIFO: the one most likely just added
        # by a transient spike). Guard against dropping below MIN is in _decide.
        actives = [r for r in self._replicas if r.state == ACTIVE]
        if not actives:
            return
        r = actives[-1]
        r.state = DRAINING
        r.state_since = time.time()
        self._log(f"[autoscaler] SCALE DOWN -> w{r.index} draining "
                  f"({self._drain_timeout()}s for in-flight connections)")
        self.scale_down_events += 1
        # Remove it from the pool first so no new connection is routed to it.
        self._sync_backends()

    def _advance_lifecycle(self):
        # Promote warmed replicas and remove fully-drained ones. Returns nothing;
        # re-syncs the pool if membership changed.
        now = time.time()
        changed = False
        removed = []
        for r in self._replicas:
            if r.state == WARMING and now - r.state_since >= self._warmup():
                r.state = ACTIVE
                r.state_since = now
                self._log(f"[autoscaler] w{r.index} warmed up -> active")
                changed = True
            elif r.state == DRAINING and now - r.state_since >= self._drain_timeout():
                r.end = now
                self._bill_quantum_shortfall(r)
                self._log(f"[autoscaler] w{r.index} drained -> removed "
                          f"(lived {r.end - r.start:.0f}s)")
                r.node.delete()
                removed.append(r)
        if removed:
            self._replicas = [r for r in self._replicas if r not in removed]
            changed = True
        if changed:
            self._sync_backends()

    def _sync_backends(self):
        # Only ACTIVE replicas carry traffic (warming/draining are out of pool).
        lines = [f"{cfg.web_ip(r.index)}:{cfg.TARGET_PORT}"
                 for r in self._replicas if r.state == ACTIVE]
        with open(LB_BACKENDS_LOCAL, 'w') as f:
            f.write('\n'.join(lines) + '\n')
        subprocess.run(
            f"docker cp {LB_BACKENDS_LOCAL} {self._lb_name}:{LB_BACKENDS_REMOTE}",
            shell=True, capture_output=True)

    def provision_initial(self):
        # The baseline pool is pre-warmed (serving from the start).
        for _ in range(cfg.MIN_REPLICAS):
            self._provision(immediate=True)

    # --- Pricing helpers --------------------------------------------------
    def _warmup(self):
        return getattr(cfg, 'WARMUP_SECONDS', 30)

    def _drain_timeout(self):
        return getattr(cfg, 'DRAIN_TIMEOUT_SECONDS', 30)

    def _p(self, key, default):
        return self._price.get(key, default)

    def _bill_quantum_shortfall(self, r):
        # A replica billed per tick for its real life; if that life is shorter
        # than the quantum, top it up to the full quantum here (the yo-yo tax).
        quantum = self._p('billing_quantum_sec', 60.0)
        life = (r.end or time.time()) - r.start
        if life < quantum:
            shortfall = quantum - life
            self.replica_seconds += shortfall
            extra = shortfall * self._p('p_vm_per_sec', 0.0)
            self.vm_cost += extra
            self.quantum_penalty += extra
            self.cost = self.vm_cost + self.lcu_cost + self.bw_cost
            self._log(f"[autoscaler] w{r.index} billed quantum: "
                      f"+{shortfall:.0f}s (lived {life:.0f}s < {quantum:.0f}s)")

    # --- Load signal ------------------------------------------------------
    def _read_lb_file(self, remote_path):
        out = subprocess.run(
            f"docker exec {self._lb_name} cat {remote_path}",
            shell=True, capture_output=True).stdout.decode('utf8', 'ignore').strip()
        if out:
            try:
                return int(out.splitlines()[0])
            except ValueError:
                pass
        return None

    def _active_connections(self):
        # Preferred signal: the LB's own count of live client connections
        # (/home/lb_conns) -- ground truth, unlike `ss`, which collapses to ~0
        # right when the attack saturates the backend.
        value = self._read_lb_file('/home/lb_conns')
        if value is not None:
            return value
        # Fallback: count established inbound connections on the VIP via `ss`.
        out = subprocess.run(
            f"docker exec {self._lb_name} ss -Hnt state established "
            f"'( sport = :{cfg.TARGET_PORT} )'",
            shell=True, capture_output=True).stdout.decode('utf8', 'ignore')
        return len([ln for ln in out.splitlines() if ln.strip()])

    def _counts(self):
        warming = sum(1 for r in self._replicas if r.state == WARMING)
        active = sum(1 for r in self._replicas if r.state == ACTIVE)
        draining = sum(1 for r in self._replicas if r.state == DRAINING)
        return warming, active, draining

    # --- Decision loop ----------------------------------------------------
    def _decide(self, conns, active_count):
        warming, active, draining = self._counts()
        provisionable = warming + active     # desired footprint (ignores draining)
        non_draining = warming + active
        per_replica = conns / max(active_count, 1)
        # Debounce scale-down: count consecutive low samples so a single dip can't
        # tear down capacity mid-attack.
        if per_replica < cfg.SCALE_DOWN_CONN:
            self._low_streak += 1
        else:
            self._low_streak = 0
        if time.time() - self._last_action < cfg.COOLDOWN_SECONDS:
            return
        low_ticks = getattr(cfg, 'SCALE_DOWN_TICKS', 3)
        if per_replica > cfg.SCALE_UP_CONN and provisionable < cfg.MAX_REPLICAS:
            self._provision()
            self._last_action = time.time()
            self._low_streak = 0
        elif self._low_streak >= low_ticks and non_draining > cfg.MIN_REPLICAS:
            self._decommission()
            self._last_action = time.time()
            self._low_streak = 0

    def run(self):
        header = ("timestamp,active_replicas,active_conns,cumulative_cost,"
                  "warming,active,draining,new_conns_per_s,bytes_per_s,"
                  "vm_cost,lcu_cost,bw_cost\n")
        with open(cfg.COST_CSV, 'w') as f:
            f.write(header)
        # Prime the rate baselines with a REAL reading before the loop. Without
        # this, the first tick differenced from 0 over a ~millisecond dt made the
        # new-connections/s rate (and thus the LCU charge) explode by ~1e6x.
        self._prev_time = time.time()
        self._prev_accepted = self._read_lb_file('/home/lb_total_conns') or 0
        self._prev_bytes = self._read_lb_file('/home/lb_bytes') or 0
        while self._running:
            self._advance_lifecycle()
            now = time.time()
            conns = self._active_connections()
            warming, active, draining = self._counts()
            billed = warming + active + draining  # every alive replica is billed

            # Rates for the LCU model (differenced from the LB's cumulative
            # counters). dt guards against a zero/!=TICK interval.
            dt = now - self._prev_time
            if dt <= 0:
                dt = cfg.TICK_SECONDS
            accepted = self._read_lb_file('/home/lb_total_conns') or 0
            nbytes = self._read_lb_file('/home/lb_bytes') or 0
            d_acc = max(0, accepted - self._prev_accepted)
            d_bytes = max(0, nbytes - self._prev_bytes)
            new_conns_per_s = d_acc / dt
            bytes_per_s = d_bytes / dt
            self._prev_time = now
            self._prev_accepted = accepted
            self._prev_bytes = nbytes

            # Bill the interval that just elapsed, using the REAL dt (not a fixed
            # TICK). For the rate-based LCU term this makes the dt cancel out --
            # p_lcu * (d_acc/dt / newdim) * dt = p_lcu * d_acc/newdim -- so a short
            # or jittery interval bills the actual connections, never a blow-up.
            self.replica_seconds += billed * dt
            self.vm_cost += billed * self._p('p_vm_per_sec', 0.0) * dt
            lcu = max(conns / self._p('lcu_active_conn_dim', 3000.0),
                      new_conns_per_s / self._p('lcu_new_conn_dim', 25.0))
            self.lcu_cost += self._p('p_lcu_per_sec', 0.0) * lcu * dt
            self.bw_cost += self._p('p_bw_per_gb', 0.0) * (d_bytes / 1e9)
            self.cost = self.vm_cost + self.lcu_cost + self.bw_cost

            with open(cfg.COST_CSV, 'a') as f:
                f.write(f"{now:.0f},{billed},{conns},{self.cost:.6f},"
                        f"{warming},{active},{draining},"
                        f"{new_conns_per_s:.2f},{bytes_per_s:.0f},"
                        f"{self.vm_cost:.6f},{self.lcu_cost:.6f},{self.bw_cost:.6f}\n")
            self._log(f"[autoscaler] billed={billed} (w{warming}/a{active}/d{draining}) "
                      f"conns={conns} new/s={new_conns_per_s:.1f} cost={self.cost:.4f}")
            self._decide(conns, active)
            time.sleep(cfg.TICK_SECONDS)

    # --- Teardown ---------------------------------------------------------
    def stop(self):
        self._running = False

    def delete_replicas(self):
        now = time.time()
        for r in self._replicas:
            if r.end is None:
                r.end = now
                self._bill_quantum_shortfall(r)
            r.node.delete()
        self._replicas = []

    def summary(self):
        p = self._price
        return (
            "\n[LST2.0][EDoS] ===== Cost report =====\n"
            f"  Pricing profile   : {p.get('cloud', 'n/a')}\n"
            f"  Scale-up events   : {self.scale_up_events}\n"
            f"  Scale-down events : {self.scale_down_events}\n"
            f"  Replica-seconds   : {self.replica_seconds:.0f} "
            f"(incl. {self.quantum_penalty / max(self._p('p_vm_per_sec', 1e-9), 1e-9):.0f}s "
            f"billed by the quantum)\n"
            f"  Compute cost      : {self.vm_cost:.4f}\n"
            f"  ALB LCU cost      : {self.lcu_cost:.4f}\n"
            f"  Bandwidth cost    : {self.bw_cost:.4f}\n"
            f"  TOTAL cost        : {self.cost:.4f}\n"
            f"  Detailed timeline : {cfg.COST_CSV}\n"
        )
