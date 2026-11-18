"""Safe link downloads: links must never reach private or internal addresses.

The tests never use the real internet. `fake_dns` replaces the address lookup
with a small table of made-up website names.
"""

import gzip
import ipaddress
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from document_intelligence_rag import fetch
from document_intelligence_rag.loaders import LoadError, load_url

FAKE_DNS = {
    "public.test": ["93.184.215.14"],
    "site.test": ["127.0.0.1"],  # the local test website, see `site`
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


# ---- downloads from a real (local) web server -----------------------------------
class Handler(BaseHTTPRequestHandler):
    """A tiny website for the tests. Each path does one thing."""

    def do_GET(self):
        if self.path == "/page":
            self.reply(200, b"<html><head><title>Hello</title></head><body><p>Public page</p></body></html>")
        elif self.path == "/to-page":
            self.redirect("/page")
        elif self.path == "/to-metadata":
            self.redirect("http://169.254.169.254/latest/meta-data/")
        elif self.path == "/to-intranet":
            self.redirect("http://intranet.test/")
        elif self.path == "/host":
            self.reply(200, self.headers["Host"].encode(), "text/plain")
        elif self.path == "/big":
            self.reply(200, b"x" * 5000, "text/plain")
        elif self.path == "/big-no-size":
            self.stream([b"x" * 1000] * 5)
        elif self.path == "/zip-bomb":
            body = gzip.compress(b"x" * 50_000)  # small to send, big once unzipped
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/slow":
            self.stream([b"x"] * 20, pause=0.1)
        elif self.path == "/loop":
            self.redirect("/loop")
        else:
            self.reply(404, b"not found")

    def reply(self, status, body, content_type="text/html; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def stream(self, pieces, pause=0.0):
        """Send the body piece by piece, without saying its size first."""
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        for piece in pieces:
            self.wfile.write(piece)
            self.wfile.flush()
            time.sleep(pause)
        self.close_connection = True

    def redirect(self, location):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):  # keep test output quiet
        pass


class QuietServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        pass  # some tests stop downloads early, which breaks the connection on purpose


@pytest.fixture
def site(fake_dns, monkeypatch):
    """Start the test website on this machine and return its address.

    The website runs on 127.0.0.1, which the app normally refuses. For these
    tests, 127.0.0.1 stands in for a public website; everything else is checked
    as usual.
    """
    real_is_public = fetch.is_public
    monkeypatch.setattr(fetch, "is_public", lambda ip: str(ip) == "127.0.0.1" or real_is_public(ip))

    server = QuietServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_download_a_public_page(site):
    download = fetch.fetch(f"{site}/page")
    assert "Public page" in download.text
    assert download.content_type.startswith("text/html")


def test_load_url_keeps_the_page_text(site):
    doc = load_url(f"{site}/page", added_by="tester")
    assert doc.text.startswith("Hello")  # the page title comes first
    assert "Public page" in doc.text


def test_redirects_to_public_pages_are_followed(site):
    download = fetch.fetch(f"{site}/to-page")
    assert download.url == f"{site}/page"
    assert "Public page" in download.text


@pytest.mark.parametrize("path", ["/to-metadata", "/to-intranet"])
def test_redirects_to_private_addresses_are_refused(site, path):
    with pytest.raises(fetch.UnsafeLinkError):
        fetch.fetch(f"{site}{path}")


def test_endless_redirects_stop(site):
    with pytest.raises(fetch.FetchError, match="redirects more than"):
        fetch.fetch(f"{site}/loop")


def test_missing_page_gives_an_error(site):
    with pytest.raises(fetch.FetchError, match="404"):
        fetch.fetch(f"{site}/nothing-here")


def test_download_connects_to_the_checked_address(site):
    # Only our fake lookup knows site.test, so this works only if the download
    # uses the address we checked instead of looking the name up again.
    port = site.rsplit(":", 1)[1]
    download = fetch.fetch(f"http://site.test:{port}/host")
    # The website is still told which site we asked for.
    assert download.text == f"site.test:{port}"


def test_the_name_is_looked_up_once_per_address(site, monkeypatch):
    """A name that changes its answer between lookups (DNS rebinding) can't trick us.

    The first lookup gives the website; any later one would give a private
    address. Since we connect to the first answer, the second never happens.
    """
    answers = [[ipaddress.ip_address("127.0.0.1")], [ipaddress.ip_address("10.0.0.5")]]
    lookups = []

    def changing_resolve(host, port):
        lookups.append(host)
        return answers[len(lookups) - 1]

    monkeypatch.setattr(fetch, "resolve", changing_resolve)
    port = site.rsplit(":", 1)[1]
    assert "Public page" in fetch.fetch(f"http://rebind.test:{port}/page").text
    assert lookups == ["rebind.test"]


@pytest.mark.parametrize("path", ["/big", "/big-no-size", "/zip-bomb"])
def test_downloads_that_are_too_big_are_stopped(site, monkeypatch, path):
    monkeypatch.setattr(fetch, "MAX_DOWNLOAD_BYTES", 1000)
    with pytest.raises(fetch.FetchError, match="larger than"):
        fetch.fetch(f"{site}{path}")


def test_downloads_that_are_too_slow_are_stopped(site, monkeypatch):
    monkeypatch.setattr(fetch, "MAX_SECONDS", 0.5)
    with pytest.raises(fetch.FetchError, match="took too long"):
        fetch.fetch(f"{site}/slow")
