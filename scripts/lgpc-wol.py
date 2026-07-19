#!/usr/bin/env python3
# Wake-on-LAN: magic packet on UDP port 9, sent both as a broadcast and
# routed to the TV's IP. Broadcast is the reliable path on the TV's own
# subnet: unicast to a sleeping TV needs an ARP reply it doesn't always
# give - the packet is silently dropped. The routed copy covers TVs on
# another subnet/VLAN, where broadcast can't reach; WebOS networked standby
# answers ARP, so routed unicast works there (issue #12). Each copy is a
# harmless no-op in the other's setup, and the packet is MAC-addressed so
# duplicates cannot wake anything else.
# Send failures are swallowed per copy: the caller's wake loop resends
# until the TV's own state proves a packet has bitten, and e.g.
# ENETUNREACH right after resume on one path must not block the other.
#
# Usage: lgpc-wol.py <MAC> <IP>

import socket, sys

try:
    mac = bytes.fromhex(sys.argv[1].replace(":", "").replace("-", ""))
except ValueError:
    mac = b""

if len(mac) != 6:
    raise SystemExit(f"invalid LGTV_MAC {sys.argv[1]!r}")

packet = b"\xff" * 6 + mac * 16

for dest, broadcast in ((("255.255.255.255", 9), True), ((sys.argv[2], 9), False)):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            if broadcast:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            s.sendto(packet, dest)
    except OSError:
        pass
