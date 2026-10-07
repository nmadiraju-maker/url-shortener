"""Which address is the real client?

Behind a load balancer every connection comes from the proxy, and the client address is in
X-Forwarded-For (XFF). Anyone can send that header, so it is believed only when the direct peer is a
configured trusted proxy. Each proxy APPENDS the address it received the request from, so the header
is read right to left, skipping trusted proxies: the first untrusted address is the client.

Reading it left to right (taking the first entry) is the classic mistake: a client simply sends
"X-Forwarded-For: <any IP>" and the proxy appends the real one after it.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable

from starlette.requests import Request


class TrustedProxies:
    def __init__(self, cidrs: Iterable[str] = ()) -> None:
        self.networks = tuple(ipaddress.ip_network(c, strict=False) for c in cidrs)

    def is_trusted(self, address: str) -> bool:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return False
        return any(ip in network for network in self.networks)

    def client_ip(self, peer: str | None, forwarded_for: str | None) -> str | None:
        """Return the client address given the direct peer and the (joined) X-Forwarded-For value."""
        if peer is None or not self.networks or not forwarded_for or not self.is_trusted(peer):
            return peer
        hops = [hop.strip() for hop in forwarded_for.split(",") if hop.strip()]
        for hop in reversed(hops):
            try:
                ipaddress.ip_address(hop)
            except ValueError:
                return peer            # malformed entry: trust nothing beyond what we can verify
            if not self.is_trusted(hop):
                return hop
        return hops[0] if hops else peer   # every hop is a trusted proxy: the request started inside


def request_client_ip(request: Request, proxies: TrustedProxies) -> str | None:
    peer = request.client.host if request.client else None
    forwarded = ", ".join(request.headers.getlist("x-forwarded-for")) or None   # multiple headers = one list
    return proxies.client_ip(peer, forwarded)
