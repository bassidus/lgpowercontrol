#!/usr/bin/env python3

import socket, sys

mac = bytes.fromhex(sys.argv[1].replace(":", "").replace("-", ""))

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
