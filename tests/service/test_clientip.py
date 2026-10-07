import pytest
from starlette.requests import Request

from urlshort.web.clientip import TrustedProxies, request_client_ip

LB = TrustedProxies(["10.0.0.0/8", "fd00::/8"])


@pytest.mark.parametrize("proxies,peer,xff,expected", [
    (TrustedProxies(), "203.0.113.9", "6.6.6.6", "203.0.113.9"),          # no proxies configured: header ignored
    (LB, "203.0.113.9", "6.6.6.6", "203.0.113.9"),                        # untrusted peer cannot set its IP
    (LB, "10.0.0.1", None, "10.0.0.1"),                                   # trusted peer, no header
    (LB, "10.0.0.1", "203.0.113.9", "203.0.113.9"),                       # one proxy
    (LB, "10.0.0.1", "6.6.6.6, 203.0.113.9", "203.0.113.9"),              # SPOOF: client prepended a fake IP
    (LB, "10.0.0.1", "203.0.113.9, 10.0.0.2", "203.0.113.9"),             # chain of trusted proxies
    (LB, "10.0.0.1", "10.0.0.3, 10.0.0.2", "10.0.0.3"),                   # request started inside the network
    (LB, "10.0.0.1", "203.0.113.9:4711", "10.0.0.1"),                     # malformed (port) -> trust nothing
    (LB, "10.0.0.1", "garbage, 203.0.113.9", "203.0.113.9"),             # garbage beyond a valid untrusted hop
    (LB, "10.0.0.1", " , 203.0.113.9 ,", "203.0.113.9"),                 # blank entries ignored
    (LB, "10.0.0.1", " , ", "10.0.0.1"),                                  # nothing usable
    (LB, "fd00::1", "2001:db8::7", "2001:db8::7"),                        # IPv6
    (LB, None, "203.0.113.9", None),                                      # no peer at all
])
def test_client_ip(proxies: TrustedProxies, peer: str | None, xff: str | None, expected: str | None) -> None:
    assert proxies.client_ip(peer, xff) == expected


def test_is_trusted_handles_non_addresses() -> None:
    assert LB.is_trusted("10.1.2.3") and not LB.is_trusted("203.0.113.9") and not LB.is_trusted("not-an-ip")


def request(client: tuple[str, int] | None, *xff: str) -> Request:
    headers = [(b"x-forwarded-for", v.encode()) for v in xff]
    return Request({"type": "http", "client": client, "headers": headers})


def test_multiple_forwarded_headers_are_one_list() -> None:
    # Two X-Forwarded-For headers must be read as one combined list, or the spoof check is bypassed.
    assert request_client_ip(request(("10.0.0.1", 1), "6.6.6.6", "203.0.113.9"), LB) == "203.0.113.9"


def test_request_without_peer() -> None:
    assert request_client_ip(request(None, "203.0.113.9"), LB) is None
