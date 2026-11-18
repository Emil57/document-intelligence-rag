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
from dataclasses import dataclass

import httpx

TIMEOUT = 20  # seconds
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
    """Download a link, but only from public internet addresses."""
    check_url(url)
    try:
        response = httpx.get(url, follow_redirects=True, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
        response.raise_for_status()
    except httpx.HTTPError as e:
        raise FetchError(f"Couldn't download {url}: {e}") from e
    return Download(
        url=str(response.url),
        content_type=response.headers.get("content-type", "").lower(),
        content=response.content,
        encoding=response.encoding or "utf-8",
    )
