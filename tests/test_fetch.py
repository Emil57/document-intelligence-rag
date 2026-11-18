"""Safe link downloads: links must never reach private or internal addresses.

The tests never use the real internet. `fake_dns` replaces the address lookup
with a small table of made-up website names.
"""

import ipaddress

import pytest

from document_intelligence_rag import fetch
from document_intelligence_rag.loaders import LoadError, load_url

FAKE_DNS = {
    "public.test": ["93.184.215.14"],
    "intranet.test": ["10.0.0.5"],
    "localhost": ["127.0.0.1", "::1"],
    # Some names point to several addresses; one private one is enough to refuse.
    "mixed.test": ["93.184.215.14", "192.168.1.10"],
}


@pytest.fixture
def fake_dns(monkeypatch):
    def resolve(host, port):
        try:
            return [ipaddress.ip_address(host)]  # the link already uses an IP address
        except ValueError:
            pass
        if host not in FAKE_DNS:
            raise fetch.FetchError(f"Couldn't find the website {host}.")
        return [ipaddress.ip_address(ip) for ip in FAKE_DNS[host]]

    monkeypatch.setattr(fetch, "resolve", resolve)


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8000/admin",
        "http://127.0.0.1/",
        "http://169.254.169.254/latest/meta-data/",  # cloud servers' secret keys live here
        "http://10.0.0.5/",
        "http://192.168.1.1/",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",  # 127.0.0.1 written as IPv6
        "http://0.0.0.0/",
        "http://intranet.test/",
        "http://mixed.test/",
    ],
)
def test_private_and_internal_links_are_refused(fake_dns, url):
    with pytest.raises(fetch.UnsafeLinkError):
        fetch.check_url(url)
    # The library refuses them too, with an error the user can read.
    with pytest.raises(LoadError, match="isn't a public website"):
        load_url(url, added_by="tester")


def test_disguised_local_addresses_are_refused():
    # The real lookup turns these into 127.0.0.1, so they're caught too.
    for url in ["http://2130706433/", "http://0x7f.1/", "http://127.1/"]:
        with pytest.raises(fetch.UnsafeLinkError):
            fetch.check_url(url)


def test_public_links_are_allowed(fake_dns):
    assert fetch.check_url("https://public.test/page") == [ipaddress.ip_address("93.184.215.14")]


@pytest.mark.parametrize("url", ["ftp://public.test/file", "file:///etc/passwd", "http:///nohost"])
def test_other_kinds_of_links_are_refused(fake_dns, url):
    with pytest.raises(fetch.UnsafeLinkError):
        fetch.check_url(url)


def test_unknown_website_gives_a_clear_error(fake_dns):
    with pytest.raises(fetch.FetchError, match="Couldn't find"):
        fetch.check_url("http://does-not-exist.test/")


def test_is_public():
    assert fetch.is_public(ipaddress.ip_address("8.8.8.8"))
    assert fetch.is_public(ipaddress.ip_address("2606:4700::1111"))
    for private in ["127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.0.1", "169.254.169.254",
                    "100.64.0.1", "224.0.0.1", "::1", "fc00::1", "fe80::1", "::ffff:10.0.0.1"]:
        assert not fetch.is_public(ipaddress.ip_address(private)), private
