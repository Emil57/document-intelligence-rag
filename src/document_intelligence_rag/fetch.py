"""Download shared links without letting them reach internal addresses (SSRF).

Our server can reach places the internet can't: localhost, its private
network, and the cloud metadata address (169.254.169.254) that hands out the
server's secret keys. A link to one of those, added by a user or by the agent
following instructions hidden in a document, could be read back through the
library. So every download goes through here and only reaches public addresses.
"""

import ipaddress
import socket
import time
from dataclasses import dataclass

import httpx

TIMEOUT = 20  # seconds per step: connecting, each piece of data
MAX_SECONDS = 60  # whole download, redirects included
MAX_REDIRECTS = 5
MAX_DOWNLOAD_BYTES = 10 * 1024 * 1024  # 10 MB, after unzipping
# Some sites, like Wikipedia, block requests without an identifying User-Agent.
USER_AGENT = "document-intelligence-rag/0.1 (https://github.com/Emil57/document-intelligence-rag)"

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


class FetchError(Exception):
    """The link couldn't be downloaded."""


class UnsafeLinkError(FetchError):
    """The link points somewhere we must not connect to."""


@dataclass
class Download:
    url: str  # after redirects
    content_type: str  # lowercased
    content: bytes
    encoding: str

    @property
    def text(self) -> str:
        return self.content.decode(self.encoding, errors="replace")


# ---- checking addresses ------------------------------------------------------
def is_public(ip: IPAddress) -> bool:
    """False for private, loopback, link-local (169.254.x) and other special addresses."""
    # "::ffff:127.0.0.1" is 127.0.0.1 written as IPv6.
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def resolve(host: str, port: int) -> list[IPAddress]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError) as e:
        raise FetchError(f"Couldn't find the website {host}.") from e
    # Strip IPv6 zone suffixes like "%eth0".
    return [ipaddress.ip_address(info[4][0].split("%")[0]) for info in infos]


def check_url(url: str) -> list[IPAddress]:
    """Raise UnsafeLinkError unless every address the link resolves to is public.

    Checks IPs rather than names, because "localhost", "2130706433" and
    "0x7f.1" all mean 127.0.0.1.
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
    # Any private address is enough to refuse: we don't control which one gets used.
    if not addresses or not all(is_public(ip) for ip in addresses):
        raise UnsafeLinkError(f"{parsed.host} isn't a public website, so it can't be added.")
    return addresses


# ---- downloading ---------------------------------------------------------------
def fetch(url: str) -> Download:
    """Download a link from public addresses only, within size and time limits.

    Redirects are followed here, not by httpx, so each new address is checked.
    We connect to the checked IP, because looking the name up again could
    give a private address the second time (DNS rebinding).
    """
    current = url
    deadline = time.monotonic() + MAX_SECONDS
    try:
        # Ignore proxy settings: a proxy would look the name up again.
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
                    current = str(httpx.URL(current).join(response.headers["location"]))
                if time.monotonic() > deadline:
                    raise FetchError(f"Couldn't download {url}: it took too long.")
    except httpx.HTTPError as e:
        raise FetchError(f"Couldn't download {url}: {e}") from e
    raise FetchError(f"Couldn't download {url}: it redirects more than {MAX_REDIRECTS} times.")


def _get_from(client: httpx.Client, url: httpx.URL, ip: IPAddress):
    """Start a streaming GET of `url` from `ip`.

    The Host header and, for HTTPS, `sni_hostname` keep the real name, so the
    site knows what we asked for and its certificate is checked against it.
    """
    extensions = {"sni_hostname": url.raw_host.decode("ascii")} if url.scheme == "https" else {}
    return client.stream(
        "GET",
        url.copy_with(host=str(ip)),
        headers={"Host": url.netloc.decode("ascii"), "User-Agent": USER_AGENT},
        extensions=extensions,
    )


def _read_body(response: httpx.Response, url: str, deadline: float) -> bytes:
    too_big = f"Couldn't download {url}: it's larger than {MAX_DOWNLOAD_BYTES // (1024 * 1024)} MB."
    declared = response.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_DOWNLOAD_BYTES:
        raise FetchError(too_big)

    # Count what arrives too, since Content-Length can be missing or wrong.
    # iter_bytes unzips, so a small "zip bomb" is counted at its full size.
    pieces, size = [], 0
    for piece in response.iter_bytes():
        size += len(piece)
        if size > MAX_DOWNLOAD_BYTES:
            raise FetchError(too_big)
        if time.monotonic() > deadline:
            raise FetchError(f"Couldn't download {url}: it took too long.")
        pieces.append(piece)
    return b"".join(pieces)
