import socket

from vidgen import net

V4 = socket.AF_INET
V6 = socket.AF_INET6


def info(family, address):
    return (family, socket.SOCK_STREAM, 6, "", (address, 443))


def test_ipv4_addresses_move_to_the_front():
    listed = [info(V6, "::1"), info(V6, "::2"), info(V4, "1.1.1.1"), info(V4, "2.2.2.2")]
    assert [i[4][0] for i in net.ipv4_first(listed)] == ["1.1.1.1", "2.2.2.2", "::1", "::2"]


def test_ipv6_stays_available_as_a_fallback():
    listed = [info(V6, "::1"), info(V4, "1.1.1.1")]
    assert {i[0] for i in net.ipv4_first(listed)} == {V4, V6}


def test_an_ipv6_only_host_is_left_alone():
    listed = [info(V6, "::1"), info(V6, "::2")]
    assert net.ipv4_first(listed) == listed


def test_the_engine_installs_it_and_lookups_still_work():
    import vidgen  # noqa: F401 - importing the engine turns it on

    assert socket.getaddrinfo is net._getaddrinfo
    net.prefer_ipv4()  # calling it again changes nothing
    assert socket.getaddrinfo is net._getaddrinfo
    results = socket.getaddrinfo("localhost", 80, type=socket.SOCK_STREAM)
    families = [r[0] for r in results]
    assert families == sorted(families, key=lambda f: f != V4)
