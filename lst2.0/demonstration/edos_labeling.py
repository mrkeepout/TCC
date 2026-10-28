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

# Brief: Flow de-duplication and labelling for the EDoS dataset (audit #1, #2).
# Pure functions over CICFlowMeter CSV rows so they can be unit-tested offline,
# away from the Docker topology. edos.py's convertPcap() calls process_rows().
#
# #1 De-dup: the load balancer proxies on the SAME /24, so every request crosses
#    br0 twice -- client<->LB (the edge) and LB<->backend (the internal hop). The
#    dataset is the EDGE viewpoint (resolved decision #1), where a real detector
#    would sit. We keep client/attacker<->LB flows and DROP any flow that touches
#    a backend replica. Keying on the backend endpoint (not "Src IP == LB") makes
#    this robust to the direction flips below.
#
# #2 Labelling: CICFlowMeter leaves Label empty/placeholder. We fill it by IP
#    range (resolved decision #2: only external attackers on .71-.79). Because
#    tshark rotates the pcap mid-capture, a long slow connection can be cut and,
#    missing its SYN, CICFlowMeter infers the WRONG direction (Src/Dst flipped).
#    So the label checks BOTH endpoints against the attacker range, never Src
#    alone -- a flipped attack flow is still labelled attack.

from typing import List, Tuple

# Host-role octet ranges on the EDoS /24 (mirror the allocation in edos_config:
# web_ip = 20+i, client_ip = 50+i, attacker_ip = 70+i, lb_vip = .10). Kept as a
# superset of the configured fleet sizes so it stays valid as they grow.
LB_OCTET = 10
BACKEND_OCTETS = range(21, 30)   # web replicas w1..w9
CLIENT_OCTETS = range(51, 60)    # benign clients l1..l9
ATTACKER_OCTETS = range(71, 80)  # external attackers a1..a9

# CICFlowMeter column headers we touch.
COL_SRC = 'Src IP'
COL_DST = 'Dst IP'
COL_LABEL = 'Label'

LABEL_ATTACK = 'attack'
LABEL_BENIGN = 'benign'
LABEL_UNKNOWN = 'unknown'


def _last_octet(ip: str):
    try:
        return int(ip.strip().split('.')[-1])
    except (ValueError, AttributeError, IndexError):
        return None


def classify(ip: str) -> str:
    """Map an IP to a host role on the testbed /24."""
    octet = _last_octet(ip)
    if octet is None:
        return 'other'
    if octet == LB_OCTET:
        return 'lb'
    if octet in BACKEND_OCTETS:
        return 'backend'
    if octet in CLIENT_OCTETS:
        return 'client'
    if octet in ATTACKER_OCTETS:
        return 'attacker'
    return 'other'


def label_for(src_ip: str, dst_ip: str) -> str:
    """Ground-truth label from BOTH endpoints (flip-robust)."""
    roles = {classify(src_ip), classify(dst_ip)}
    if 'attacker' in roles:
        return LABEL_ATTACK
    if 'client' in roles:
        return LABEL_BENIGN
    return LABEL_UNKNOWN


def is_internal_hop(src_ip: str, dst_ip: str) -> bool:
    """True if the flow touches a backend replica -- the LB<->backend internal
    hop we drop to keep only the edge viewpoint."""
    roles = {classify(src_ip), classify(dst_ip)}
    return 'backend' in roles


def process_rows(header: str, data_lines: List[str]) -> Tuple[str, List[str], dict]:
    """De-duplicate (drop the internal hop) and label a block of CICFlowMeter
    rows sharing `header`.

    Returns (out_header, kept_rows, stats). If the header has no Label column it
    is appended. Rows whose Src/Dst columns can't be located are kept unchanged
    and counted as 'malformed' so nothing is silently dropped.
    """
    fields = header.split(',')
    try:
        i_src = fields.index(COL_SRC)
        i_dst = fields.index(COL_DST)
    except ValueError:
        # Unexpected header: pass the block through untouched rather than
        # mangle it, and say so via stats.
        return header, [ln for ln in data_lines if ln.strip()], {
            'kept': 0, 'dropped_internal': 0, 'attack': 0, 'benign': 0,
            'unknown': 0, 'malformed': len([l for l in data_lines if l.strip()]),
            'no_src_dst_column': True}

    has_label = COL_LABEL in fields
    if has_label:
        i_label = fields.index(COL_LABEL)
        out_header = header
    else:
        i_label = len(fields)
        out_header = header + ',' + COL_LABEL

    stats = {'kept': 0, 'dropped_internal': 0, 'attack': 0, 'benign': 0,
             'unknown': 0, 'malformed': 0, 'no_src_dst_column': False}
    kept: List[str] = []
    width = len(fields)

    for line in data_lines:
        if not line.strip():
            continue
        if line.startswith('Flow ID') or line == header:
            # A repeated header line from a concatenated file: skip it.
            continue
        cols = line.split(',')
        if len(cols) < max(i_src, i_dst) + 1:
            kept.append(line)
            stats['malformed'] += 1
            continue
        src, dst = cols[i_src], cols[i_dst]
        if is_internal_hop(src, dst):
            stats['dropped_internal'] += 1
            continue
        label = label_for(src, dst)
        stats[label] += 1
        if has_label:
            if len(cols) <= i_label:
                cols += [''] * (i_label - len(cols) + 1)
            cols[i_label] = label
            kept.append(','.join(cols))
        else:
            # Pad short rows to the header width before appending the label, so
            # the Label value stays aligned with its column.
            if len(cols) < width:
                cols += [''] * (width - len(cols))
            kept.append(','.join(cols) + ',' + label)
        stats['kept'] += 1

    return out_header, kept, stats
