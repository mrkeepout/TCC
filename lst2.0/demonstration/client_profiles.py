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

# Brief: Configurable benign-traffic framework. Runs INSIDE a client container
# and drives well-formed HTTP toward the load balancer VIP so captured flows
# carry a realistic benign/attack contrast.
#
# This replaces the naive single-rate legit_client.py (#3 of the audit) with a
# SURGE-style analytical generator (pure Python, no Selenium): sessions over a
# Keep-Alive connection, heavy-tailed think-times, Zipf object sizes and a POST
# fraction, so RUDY's slow POST is not trivially separable by "there is a POST".
#
# The design mirrors SlowAttack (Template Method): the base class owns the whole
# lifecycle (connect, run sessions, think, reconnect, count), and each client
# PROFILE fills only the two holes that actually vary -- what one activity burst
# looks like (build_session) and how long the client idles afterwards
# (idle_after_session). "Which type of client" is then just picking a subclass.
#
# The three profiles reimplement the CADENCE of the LST 2.0 behaviours
# (browsing / mailing / printing) over HTTP, since the EDoS topology exposes a
# single HTTP service behind the VIP -- not the original Selenium/SMTP/IPP code.
#
# Usage: client_profiles.py --profile <browsing|mailing|printing> \
#            --target <vip> --port <port> [--seed N] [--client-id N] \
#            [--duration S]  (duration 0 = run until the container is killed)

from __future__ import annotations

import argparse
import http.client
import random
import sys
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List


# =============================================================================
# Configuration and the unit of work.
# =============================================================================
@dataclass
class ClientConfig:
    target_vip: str = '127.0.0.1'
    target_port: int = 80
    seed: int = 0           # per-run determinism (propagated by the orchestrator)
    client_id: int = 1      # distinguishes fleet members sharing a seed
    duration: float = 0.0   # seconds; 0 = run until killed
    connect_timeout: float = 5.0
    qos_csv: str = ''       # per-request QoS log (latency/status); '' disables


@dataclass
class Request:
    """One HTTP request inside a session."""
    method: str = 'GET'
    path: str = '/'
    body_size: int = 0            # bytes to send in the body (POST uploads)
    think_after: float = 0.0      # intra-session pause before the next request


# =============================================================================
# Base class -- the common algorithm (Template Method), mirroring SlowAttack.
# =============================================================================
class ClientProfile(ABC):
    """
    Base of the benign client profiles.

    Single responsibility: manage the lifecycle of a Keep-Alive HTTP client
    (connect, run a session, idle, reconnect on failure) and keep the ground
    truth counters. WHAT each client does is left to the subclasses in the two
    abstract methods below -- exactly the two "holes" that distinguish a
    browser from a mail client from a print client.
    """

    def __init__(self, config: ClientConfig) -> None:
        self._config = config
        # Determinism: seed mixed with client_id so two clients in the same run
        # are reproducible yet not identical (Phase 0.3 reproducibility).
        self._rng = random.Random(config.seed * 1000 + config.client_id)
        self._conn: http.client.HTTPConnection | None = None
        self._ok = 0
        self._fail = 0
        self._last_err = ''
        # QoS: one row per request (latency + status), flushed immediately so the
        # data survives even though the container is killed at teardown. The
        # orchestrator copies this file out before deleting the client. This is
        # the availability/QoS dimension -- the collateral damage the attack does
        # to the legitimate user, alongside the economic one.
        self._qos = None
        if config.qos_csv:
            try:
                self._qos = open(config.qos_csv, 'w', buffering=1)
                self._qos.write(
                    'timestamp,profile,client_id,method,path,body_size,'
                    'latency_ms,status,ok\n')
            except OSError:
                self._qos = None

    # ----- The TWO points each profile fills (what varies) -------------------
    @abstractmethod
    def build_session(self) -> List[Request]:
        """The burst of requests that makes up one activity (a page view, an
        inbox check, a print job...). Timing between them is each Request's
        think_after."""

    @abstractmethod
    def idle_after_session(self) -> float:
        """Seconds the client stays idle before the next activity -- the
        heavy-tailed, profile-specific cadence."""

    @property
    def name(self) -> str:
        return type(self).__name__

    # ----- The common algorithm (what does NOT vary) ------------------------
    def run(self) -> None:
        start = time.time()
        sys.stderr.write(
            f"[client] profile={self.name} id={self._config.client_id} "
            f"target={self._config.target_vip}:{self._config.target_port} "
            f"seed={self._config.seed}\n")
        sys.stderr.flush()
        try:
            while not self._deadline_reached(start):
                for req in self.build_session():
                    if self._deadline_reached(start):
                        break
                    self._perform(req)
                    self._bounded_sleep(req.think_after, start)
                self._report()
                self._bounded_sleep(self.idle_after_session(), start)
        except KeyboardInterrupt:
            pass
        finally:
            self._close()
            self._report(force=True)
            if self._qos is not None:
                try:
                    self._qos.close()
                except OSError:
                    pass

    def _deadline_reached(self, start: float) -> bool:
        return self._config.duration > 0 and (time.time() - start) >= self._config.duration

    def _bounded_sleep(self, seconds: float, start: float) -> None:
        """Sleep, but never past the run deadline -- otherwise a short session
        followed by a heavy-tailed (up to 40s) idle would sail well past a
        bounded run's duration."""
        if seconds <= 0:
            return
        if self._config.duration > 0:
            remaining = self._config.duration - (time.time() - start)
            seconds = min(seconds, remaining)
        if seconds > 0:
            time.sleep(seconds)

    # ----- Internal steps (one responsibility each) -------------------------
    def _connection(self) -> http.client.HTTPConnection:
        if self._conn is None:
            self._conn = http.client.HTTPConnection(
                self._config.target_vip, self._config.target_port,
                timeout=self._config.connect_timeout)
        return self._conn

    def _perform(self, req: Request) -> None:
        """Issue one request over the Keep-Alive connection. A failure (a
        saturated backend under attack) is counted, not swallowed, and the
        connection is reset so the next request reconnects."""
        t0 = time.time()
        status = ''
        ok = 0
        try:
            conn = self._connection()
            body = b'x' * req.body_size if req.body_size > 0 else None
            headers = {'Connection': 'keep-alive'}
            if body is not None:
                headers['Content-Type'] = 'application/octet-stream'
            conn.request(req.method, req.path, body=body, headers=headers)
            resp = conn.getresponse()
            resp.read()  # drain so the connection can be reused
            status = str(resp.status)
            ok = 1
            self._ok += 1
        except Exception as ex:
            self._fail += 1
            self._last_err = f"{type(ex).__name__}: {ex}"
            status = type(ex).__name__
            self._close()  # force a fresh connection next time
        self._qos_record(req, (time.time() - t0) * 1000.0, status, ok)

    def _qos_record(self, req: Request, latency_ms: float, status: str, ok: int) -> None:
        if self._qos is None:
            return
        try:
            self._qos.write(
                f"{time.time():.3f},{self.name},{self._config.client_id},"
                f"{req.method},{req.path},{req.body_size},"
                f"{latency_ms:.1f},{status},{ok}\n")
        except (OSError, ValueError):
            pass

    def _close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def _report(self, force: bool = False) -> None:
        # Surface counts so a run that produced only errors is diagnosable
        # (the old generator could look like "no traffic at all").
        if force or (self._ok + self._fail) % 10 == 0:
            sys.stderr.write(
                f"[client] profile={self.name} id={self._config.client_id} "
                f"ok={self._ok} fail={self._fail} last_err={self._last_err}\n")
            sys.stderr.flush()

    # ----- Shared samplers used by the profiles -----------------------------
    def _think(self, median: float, sigma: float = 0.6,
               lo: float = 1.0, hi: float = 40.0) -> float:
        """Heavy-tailed think-time (lognormal), clamped to a CIDDS-like 1..40s
        range. `median` is the lognormal median in seconds."""
        import math
        value = self._rng.lognormvariate(math.log(median), sigma)
        return max(lo, min(hi, value))

    def _zipf_object_size(self) -> int:
        """Pick an object size (bytes) from a small Zipf-weighted menu, so GET
        sizes are skewed to many small / few large objects like the web."""
        sizes = [1024, 4096, 16384, 65536, 262144]       # 1KB .. 256KB
        weights = [1.0 / (rank + 1) for rank in range(len(sizes))]  # Zipf-ish
        return self._rng.choices(sizes, weights=weights, k=1)[0]

    def _geometric_count(self, p_stop: float, max_n: int) -> int:
        """Number of repetitions drawn like the LST `while randint()!=0` loops:
        keep going with probability (1 - p_stop), at least 1, capped at max_n."""
        n = 1
        while n < max_n and self._rng.random() > p_stop:
            n += 1
        return n


# =============================================================================
# Profiles -- the CADENCE of the three LST 2.0 behaviours, over HTTP.
# =============================================================================
class Browsing(ClientProfile):
    """LST `browsing`: a page view = the HTML plus a few embedded objects over
    one Keep-Alive connection, then a heavy-tailed think-time before the next
    page. A fraction of page views submit a form (POST) so POSTs are not an
    attack-only signal."""

    def build_session(self) -> List[Request]:
        reqs: List[Request] = [Request('GET', '/', think_after=self._think(0.5, lo=0.1, hi=3.0))]
        embedded = self._rng.randint(2, 5)  # 3..6 requests per page total
        for _ in range(embedded):
            size = self._zipf_object_size()
            reqs.append(Request('GET', f'/?obj={size}',
                                think_after=self._rng.uniform(0.05, 0.4)))
        if self._rng.random() < 0.20:       # ~20% of pages submit a form
            reqs.append(Request('POST', '/submit', body_size=self._rng.randint(256, 2048)))
        return reqs

    def idle_after_session(self) -> float:
        return self._think(6.0)  # seconds between page views


class Mailing(ClientProfile):
    """LST `mailing`: an inbox check = a burst of fetches (the original repeats
    `while randint(0,3)!=0`), and roughly every other visit sends a message with
    an attachment (a larger POST). Long idle between mail sessions."""

    def build_session(self) -> List[Request]:
        reqs: List[Request] = []
        fetches = self._geometric_count(p_stop=0.25, max_n=4)  # mirrors randint(0,3)
        for _ in range(fetches):
            reqs.append(Request('GET', '/?mail=fetch',
                                think_after=self._rng.uniform(0.5, 2.0)))
        if self._rng.random() < 0.5:        # compose + send with an attachment
            attachment = self._rng.randint(16384, 131072)  # 16KB .. 128KB
            reqs.append(Request('POST', '/?mail=send', body_size=attachment))
        return reqs

    def idle_after_session(self) -> float:
        return self._think(20.0, hi=40.0)  # mail is checked infrequently


class Printing(ClientProfile):
    """LST `printing`: a geometric number of print jobs (`while randint(0,1)!=0`),
    each a document upload (POST) scaled by 1..10 copies, ~10s between jobs. Long
    idle between print sessions -- printing is rare and bursty."""

    def build_session(self) -> List[Request]:
        reqs: List[Request] = []
        jobs = self._geometric_count(p_stop=0.5, max_n=5)  # mirrors randint(0,1)
        for _ in range(jobs):
            copies = self._rng.randint(1, 10)
            document = self._rng.randint(32768, 98304)     # 32KB .. 96KB base
            reqs.append(Request('POST', '/?print=job', body_size=document * copies,
                                think_after=10.0))          # ~10s between jobs
        return reqs

    def idle_after_session(self) -> float:
        return self._think(25.0, hi=40.0)


# Peso por atividade no perfil misto: um usuário navega muito, envia e-mail com
# frequência média e imprime pouco. Ajustável sem tocar na lógica.
ACTIVITY_WEIGHTS = {'browsing': 0.6, 'mailing': 0.3, 'printing': 0.1}


class MixedUser(ClientProfile):
    """Usuário benigno misto: a cada sessão sorteia UMA entre browsing, mailing e
    printing (pesada por ACTIVITY_WEIGHTS) e emite aquele burst, intercalando as
    três ao longo da vida do cliente -- como um usuário real que navega, checa
    e-mail e imprime na mesma sessão de trabalho.

    Compõe as três atividades já existentes em vez de reimplementá-las: delega
    build_session()/idle_after_session() à atividade sorteada. Todas as atividades
    compartilham o MESMO self._rng (ver _make_activity), senão o run deixaria de
    ser reproduzível."""

    def __init__(self, config: ClientConfig) -> None:
        super().__init__(config)
        self._acts = {
            'browsing': self._make_activity(Browsing, config),
            'mailing':  self._make_activity(Mailing, config),
            'printing': self._make_activity(Printing, config),
        }
        self._names = list(self._acts)
        self._weights = [ACTIVITY_WEIGHTS[n] for n in self._names]
        self._last = self._names[0]

    def _make_activity(self, cls, config: ClientConfig) -> ClientProfile:
        """Instancia uma atividade e injeta o RNG do MixedUser, para que o sorteio
        de atividade e os samplers de todas as três consumam a MESMA sequência
        determinística (reprodutibilidade por seed/client_id)."""
        act = cls(config)
        act._rng = self._rng
        return act

    def build_session(self) -> List[Request]:
        self._last = self._rng.choices(self._names, weights=self._weights, k=1)[0]
        sys.stderr.write(
            f"[client] profile={self.name} id={self._config.client_id} "
            f"activity={self._last}\n")
        sys.stderr.flush()
        return self._acts[self._last].build_session()

    def idle_after_session(self) -> float:
        return self._acts[self._last].idle_after_session()


# Registry: the name used in the FLEET config -> the profile class.
PROFILES = {
    'browsing': Browsing,
    'mailing': Mailing,
    'printing': Printing,
    'mixed': MixedUser,
}


def build_profile(name: str, config: ClientConfig) -> ClientProfile:
    try:
        return PROFILES[name](config)
    except KeyError:
        raise SystemExit(
            f"[client] unknown profile '{name}'. "
            f"Known: {', '.join(sorted(PROFILES))}")


def _parse_args(argv: List[str]) -> ClientConfig:
    p = argparse.ArgumentParser(description='Benign HTTP client profile')
    p.add_argument('--profile', required=True, choices=sorted(PROFILES))
    p.add_argument('--target', required=True)
    p.add_argument('--port', type=int, default=80)
    p.add_argument('--seed', type=int, default=0)
    p.add_argument('--client-id', type=int, default=1)
    p.add_argument('--duration', type=float, default=0.0)
    p.add_argument('--qos-csv', default='')
    a = p.parse_args(argv)
    cfg = ClientConfig(target_vip=a.target, target_port=a.port, seed=a.seed,
                       client_id=a.client_id, duration=a.duration,
                       qos_csv=a.qos_csv)
    return a.profile, cfg


def main(argv: List[str]) -> None:
    profile_name, cfg = _parse_args(argv)
    build_profile(profile_name, cfg).run()


if __name__ == '__main__':
    main(sys.argv[1:])
