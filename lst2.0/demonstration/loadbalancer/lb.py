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

# Brief: Minimal round-robin TCP load balancer used as the single entry point
# (VIP) of the emulated cloud service. The backend pool is read from a plain
# text file on EVERY new connection, so the autoscaler only has to rewrite that
# file (via `docker cp`) to add/remove replicas -- no restart, no signals.
#
# Usage: python3 lb.py <listen_ip> <listen_port> <backends_file>
# backends_file: one "ip:port" per line (blank lines / '#' comments ignored).

import socket
import sys
import threading
import time
import itertools

BUFFER = 65536

# Ground-truth count of client connections currently held open by the LB. The
# autoscaler reads this (via the file below) instead of inferring load with `ss`,
# which only sees a fragile kernel snapshot. Written by a background thread.
CONN_COUNT = 0
_count_lock = threading.Lock()
CONN_COUNT_FILE = '/home/lb_conns'


def _adjust_count(delta):
    global CONN_COUNT
    with _count_lock:
        CONN_COUNT += delta


def _publish_count_loop():
    # Persist the live count roughly once a second so the autoscaler can read it.
    while True:
        try:
            with _count_lock:
                value = CONN_COUNT
            with open(CONN_COUNT_FILE, 'w') as f:
                f.write(f"{value}\n")
        except OSError:
            pass
        time.sleep(1)


def read_backends(path):
    backends = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                host, _, port = line.partition(':')
                backends.append((host, int(port) if port else 80))
    except FileNotFoundError:
        pass
    return backends


# Round-robin cursor shared across connections (only the index is shared; the
# backend list itself is re-read per connection so scaling changes take effect).
_counter = itertools.count()
_lock = threading.Lock()


def pick_backend(backends):
    with _lock:
        idx = next(_counter)
    return backends[idx % len(backends)]


def pipe(src, dst):
    try:
        while True:
            data = src.recv(BUFFER)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        for s in (src, dst):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def handle(client, backends_path):
    # Count this connection for its whole lifetime: this runs in its own thread
    # (spawned per accept), so joining the relay below does not block accepts.
    _adjust_count(1)
    try:
        backends = read_backends(backends_path)
        if not backends:
            client.close()
            return
        host, port = pick_backend(backends)
        try:
            upstream = socket.create_connection((host, port), timeout=10)
        except OSError:
            client.close()
            return
        # Bidirectional relay. One thread each way; connections are held open for
        # as long as the client keeps them (this is what a Slowloris exploits).
        t1 = threading.Thread(target=pipe, args=(client, upstream), daemon=True)
        t2 = threading.Thread(target=pipe, args=(upstream, client), daemon=True)
        t1.start()
        t2.start()
        # Block until the relay is done so the count reflects the live connection.
        t1.join()
        t2.join()
    finally:
        _adjust_count(-1)


def main():
    listen_ip = sys.argv[1]
    listen_port = int(sys.argv[2])
    backends_path = sys.argv[3]

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((listen_ip, listen_port))
    server.listen(1024)
    sys.stderr.write(f"[lb] listening on {listen_ip}:{listen_port}\n")
    sys.stderr.flush()

    # Publish the live open-connection count for the autoscaler to read.
    threading.Thread(target=_publish_count_loop, daemon=True).start()

    while True:
        try:
            client, _ = server.accept()
        except OSError:
            continue
        threading.Thread(target=handle, args=(client, backends_path), daemon=True).start()


if __name__ == '__main__':
    main()
