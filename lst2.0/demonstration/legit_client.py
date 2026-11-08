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

# Brief: Benign background traffic generator with Poisson think-time and
# multi-endpoint heavy-tailed file size distribution (Paxson & Floyd 1995,
# Crovella & Bestavros 1997).
#
# Usage: legit_client.py <target_vip> <port> [mean_interval_seconds]

import random
import sys
import time
import urllib.request

ENDPOINTS = [
    "/file1_200b.txt",
    "/file2_500b.txt",
    "/file3_1kb.txt",
    "/file4_2.5kb.html",
    "/file5_5kb.js",
    "/file6_12kb.css",
    "/file7_25kb.png",
    "/file8_50kb.jpg",
    "/file9_90kb.pdf",
    "/file10_150kb.bin",
    "/index.html"
]


def main():
    vip = sys.argv[1]
    port = int(sys.argv[2])
    mean_interval = float(sys.argv[3]) if len(sys.argv) > 3 else 3.0

    ok = 0
    fail = 0
    last_err = ''
    while True:
        # Sorteia um endpoint entre os 10 arquivos de tamanhos variados
        target_path = random.choice(ENDPOINTS)
        url = f"http://{vip}:{port}{target_path}"
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                resp.read()
            ok += 1
        except Exception as ex:
            fail += 1
            last_err = f"{type(ex).__name__}: {ex}"
        if (ok + fail) % 10 == 0:
            sys.stderr.write(
                f"[legit] target={url} ok={ok} fail={fail} last_err={last_err}\n")
            sys.stderr.flush()

        # Poisson think-time estocástico: distribuição exponencial com taxa lambda = 1.0 / mean_interval
        # Clipado entre 0.2s e 12.0s para evitar extremos irrealistas
        think_time = random.expovariate(1.0 / mean_interval)
        think_time = max(0.2, min(think_time, 12.0))
        time.sleep(think_time)


if __name__ == '__main__':
    main()
