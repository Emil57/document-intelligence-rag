"""Safe downloads for links that people (or documents) share.

The server downloads every link added to a library. Those requests come from
our server, which can reach places the internet can't: programs on the server
itself (localhost), other machines on its private network, and the cloud
provider's "metadata" address (169.254.169.254), which hands out the server's
secret keys. Someone could add such a link and then ask the agent to read the
result back. This trick is called SSRF (server-side request forgery).

A link can also arrive without a person typing it: a shared document can
contain hidden instructions telling the agent to call add_link. So every
download goes through here, and we only connect to public internet addresses.
"""

import ipaddress
import socket
import time
from dataclasses import dataclass

import httpx

TIMEOUT = 20  # seconds to wait for each step (connecting, each piece of data)
MAX_SECONDS = 60  # for the whole download, redirects included
MAX_REDIRECTS = 5
MAX_DOWNLOAD_BYTES = 10 * 1024 * 1024  # 10 MB, after unzipping
# Sites like Wikipedia block downloads from unnamed programs. Naming the app
# and linking the project is the polite, expected way to identify ourselves.
USER_AGENT = "document-intelligence-rag/0.1 (https://github.com/Emil57/document-intelligence-rag)"

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


class FetchError(Exception):
    """The link couldn't be downloaded."""


class UnsafeLinkError(FetchError):
    """The link points somewhere we must not connect to."""


@dataclass
class Download:
    url: str           # where the content came from
    content_type: str  # e.g. "text/html; charset=utf-8", lowercased
    content: bytes
    encoding: str      # how to turn the bytes into text

    @property
    def text(self) -> str:
        return self.content.decode(self.encoding, errors="replace")


# ---- checking addresses ------------------------------------------------------
def is_public(ip: IPAddress) -> bool:
    """True for normal internet addresses.

    is_global is False for private networks (10.x, 192.168.x, ...), this
    machine (127.x, ::1), link-local addresses such as 169.254.169.254, and
    other special ranges.
    """
    # "::ffff:127.0.0.1" is 127.0.0.1 written as IPv6. Check the IPv4 inside.
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def resolve(host: str, port: int) -> list[IPAddress]:
    """Look up the IP addresses a host name points to."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError) as e:
        raise FetchError(f"Couldn't find the website {host}.") from e
    # IPv6 link-local addresses can end in "%eth0"; that part isn't an address.
    return [ipaddress.ip_address(info[4][0].split("%")[0]) for info in infos]


def check_url(url: str) -> list[IPAddress]:
    """Raise UnsafeLinkError unless the link only leads to public addresses.

    We check the IP address, not the name, because names can hide where they
    point: "localhost", "2130706433" and "0x7f.1" all mean 127.0.0.1.
    """
    try:
        parsed = httpx.URL(url)
    except httpx.InvalidURL as e:
        raise UnsafeLinkError(f"That isn't a valid link: {e}") from e
    if parsed.scheme not in ("http", "https"):
        raise UnsafeLinkError("Links must start with http:// or https://")
    if not parsed.host:
        raise UnsafeLinkError("That link has no website name.")

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    addresses = resolve(parsed.host, port)
    # Refuse if *any* address is private: we don't control which one gets used.
    if not addresses or not all(is_public(ip) for ip in addresses):
        raise UnsafeLinkError(f"{parsed.host} isn't a public website, so it can't be added.")
    return addresses


# ---- downloading ---------------------------------------------------------------
def fetch(url: str) -> Download:
    """Download a link, but only from public internet addresses.

    Two tricks are blocked here:
    - Redirects: a public website can answer "go to http://127.0.0.1/ instead",
      so we follow redirects ourselves and check every new address.
    - DNS rebinding: a website name can point to a public address when we check
      it and to a private one a moment later, when the download looks it up
      again. So we connect to the exact address we checked.

    It also stops downloads that are too big or too slow, so nobody can tie up
    the server with a huge file or a website that sends one byte at a time.
    """
    current = url
    deadline = time.monotonic() + MAX_SECONDS
    try:
        # trust_env=False ignores proxy settings: a proxy would look the name
        # up again by itself, undoing the check.
        with httpx.Client(timeout=TIMEOUT, trust_env=False) as client:
            for _ in range(MAX_REDIRECTS + 1):
                addresses = check_url(current)
                with _get_from(client, httpx.URL(current), addresses[0]) as response:
                    if not response.is_redirect:
                        response.raise_for_status()
                        return Download(
                            url=current,
                            content_type=response.headers.get("content-type", "").lower(),
                            content=_read_body(response, url, deadline),
                            encoding=response.encoding or "utf-8",
                        )
                    # The new address can be relative, like "/other-page".
                    current = str(httpx.URL(current).join(response.headers["location"]))
                if time.monotonic() > deadline:
                    raise FetchError(f"Couldn't download {url}: it took too long.")
    except httpx.HTTPError as e:
        raise FetchError(f"Couldn't download {url}: {e}") from e
    raise FetchError(f"Couldn't download {url}: it redirects more than {MAX_REDIRECTS} times.")


def _get_from(client: httpx.Client, url: httpx.URL, ip: IPAddress):
    """Start a GET of `url`, connecting to `ip` instead of looking the name up again.

    Returns a "streaming" response: the body arrives piece by piece in
    _read_body, so we can stop as soon as it gets too big.

    The website still needs to know which site we want: the Host header says
    so, and for HTTPS, `sni_hostname` makes the certificate check use the real
    name too, so a site can't pretend to be another one.
    """
    extensions = {"sni_hostname": url.raw_host.decode("ascii")} if url.scheme == "https" else {}
    return client.stream(
        "GET",
        url.copy_with(host=str(ip)),
        headers={"Host": url.netloc.decode("ascii"), "User-Agent": USER_AGENT},
        extensions=extensions,
    )


def _read_body(response: httpx.Response, url: str, deadline: float) -> bytes:
    """Read the body piece by piece, stopping if it gets too big or too slow."""
    too_big = f"Couldn't download {url}: it's larger than {MAX_DOWNLOAD_BYTES // (1024 * 1024)} MB."
    declared = response.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_DOWNLOAD_BYTES:
        raise FetchError(too_big)  # no need to start downloading

    # Count what actually arrives too: the declared size can be missing or wrong.
    # iter_bytes unzips compressed pages, so a small "zip bomb" is caught as well.
    pieces, size = [], 0
    for piece in response.iter_bytes():
        size += len(piece)
        if size > MAX_DOWNLOAD_BYTES:
            raise FetchError(too_big)
        if time.monotonic() > deadline:
            raise FetchError(f"Couldn't download {url}: it took too long.")
        pieces.append(piece)
    return b"".join(pieces)
