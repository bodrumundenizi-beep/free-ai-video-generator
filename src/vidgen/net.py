"""Network plumbing shared by everything that goes online.

One job so far: try IPv4 before IPv6.

Windows lists a host's IPv6 addresses first. On a machine whose IPv6 route is
broken - common on home routers - every new connection then waits out a full
timeout per IPv6 address before falling back: about 20 seconds per host, per
render, with nothing on screen to explain it. Browsers hide this by racing
both families; ``requests`` does not.

Sorting IPv4 first costs nothing where IPv6 works, and IPv6 addresses stay in
the list, so a network that only has IPv6 still connects.
"""

from __future__ import annotations

import socket

_original_getaddrinfo = socket.getaddrinfo


def ipv4_first(addresses):
    """``addresses`` (getaddrinfo results) with IPv4 entries moved to the front.

    The order within each family is kept: resolvers sort by preference.
    """
    return sorted(addresses, key=lambda info: info[0] != socket.AF_INET)


def _getaddrinfo(*args, **kwargs):
    return ipv4_first(_original_getaddrinfo(*args, **kwargs))


def prefer_ipv4() -> None:
    """Make every lookup in this process return IPv4 addresses first. Idempotent."""
    if socket.getaddrinfo is not _getaddrinfo:
        socket.getaddrinfo = _getaddrinfo
