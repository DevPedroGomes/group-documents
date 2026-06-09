"""URL crawler with SSRF defense.

Ported from the RAG-Crawler showcase. Fetches an HTTP(S) URL using httpx (no
Playwright — JavaScript-rendered SPAs will return whatever the server responds
with on first byte) and extracts the visible text via BeautifulSoup.

The SSRF guard rejects:
  - non-http(s) schemes
  - hostnames in the explicit deny-list (localhost, cloud metadata aliases)
  - obfuscated IPv4 encodings (octal/hex/integer)
  - IPs that resolve to private/loopback/link-local/reserved/multicast networks
  - IPv6 with embedded private IPv4 (mapped, sixtofour, teredo)
  - cloud metadata IP ranges (169.254.169.254, fd00:ec2::/32, etc.)
  - sensitive ports (SSH, MySQL, Postgres, Redis, MongoDB, …)

Redirects are followed manually with re-validation on every hop, so a 30x to
an internal IP cannot bypass the check.
"""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
from typing import Optional, Tuple
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)


_DENY_HOSTS = {
    "localhost", "ip6-localhost", "ip6-loopback",
    "metadata.google.internal", "metadata", "metadata.azure.com",
    "metadata.azure.internal", "instance-data",
}

_DOTTED_QUAD = re.compile(
    r"^(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(?:\.(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}$"
)

_EXTRA_V4_NETS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
    ipaddress.ip_network("240.0.0.0/4"),
    ipaddress.ip_network("255.255.255.255/32"),
]

_EXTRA_V6_NETS = [
    ipaddress.ip_network("fd00:ec2::/32"),
]

_BLOCKED_PORTS = {22, 23, 25, 3306, 5432, 6379, 27017}

USER_AGENT = "GroupDocsBot/1.0 (+https://group-documents.pgdev.com.br)"
MAX_BYTES = 5 * 1024 * 1024  # 5 MB hard cap on response body
FETCH_TIMEOUT = 20.0
MAX_REDIRECTS = 5


def _check_ip(ip_obj: ipaddress._BaseAddress) -> Tuple[bool, str]:
    if (
        ip_obj.is_private
        or ip_obj.is_loopback
        or ip_obj.is_link_local
        or ip_obj.is_reserved
        or ip_obj.is_multicast
        or ip_obj.is_unspecified
    ):
        return False, f"private/internal IP blocked: {ip_obj}"

    if isinstance(ip_obj, ipaddress.IPv4Address):
        for net in _EXTRA_V4_NETS:
            if ip_obj in net:
                return False, f"IP in blocked range ({net}): {ip_obj}"
    else:
        embedded = None
        if ip_obj.ipv4_mapped is not None:
            embedded = ip_obj.ipv4_mapped
        elif ip_obj.sixtofour is not None:
            embedded = ip_obj.sixtofour
        elif ip_obj.teredo is not None:
            embedded = ip_obj.teredo[1]
        if embedded is not None:
            ok, err = _check_ip(embedded)
            if not ok:
                return False, f"IPv6 with embedded v4 blocked: {err}"
        for net in _EXTRA_V6_NETS:
            if ip_obj in net:
                return False, f"IP in blocked range ({net}): {ip_obj}"

    return True, ""


def _normalize_host(hostname: str) -> Tuple[Optional[str], str]:
    if not hostname:
        return None, "invalid URL: hostname missing"
    if "@" in hostname:
        return None, "invalid hostname: contains '@'"
    host = hostname.rstrip(".")
    try:
        host = host.encode("idna").decode("ascii").lower()
    except Exception:
        host = host.lower()
    return host, ""


def is_safe_url(url: str) -> Tuple[bool, str]:
    try:
        parsed = urlparse(url)

        if parsed.scheme not in ("http", "https"):
            return False, f"scheme '{parsed.scheme}' not allowed (use http or https)"

        host, err = _normalize_host(parsed.hostname or "")
        if err:
            return False, err

        if host in _DENY_HOSTS:
            return False, f"hostname blocked: {host}"

        try:
            port = parsed.port
        except ValueError:
            return False, "invalid port"
        if port and port in _BLOCKED_PORTS:
            return False, f"port {port} not allowed"

        ip_literal: Optional[ipaddress._BaseAddress] = None
        if host.startswith("[") and host.endswith("]"):
            try:
                ip_literal = ipaddress.IPv6Address(host[1:-1])
            except ValueError:
                return False, "invalid IPv6 literal"
        else:
            if "." in host and all(c.isdigit() or c == "." for c in host):
                if not _DOTTED_QUAD.match(host):
                    return False, f"obfuscated/malformed IPv4 blocked: {host}"
                try:
                    ip_literal = ipaddress.IPv4Address(host)
                except ValueError:
                    return False, f"invalid IPv4: {host}"
            else:
                try:
                    ip_literal = ipaddress.IPv6Address(host)
                except ValueError:
                    pass

        if ip_literal is not None:
            ok, err = _check_ip(ip_literal)
            if not ok:
                return False, err
            return True, ""

        try:
            infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
        except socket.gaierror as e:
            return False, f"DNS lookup failed for {host}: {e}"

        if not infos:
            return False, f"no DNS records for {host}"

        seen = set()
        for info in infos:
            sockaddr = info[4]
            ip_str = sockaddr[0]
            if "%" in ip_str:
                ip_str = ip_str.split("%", 1)[0]
            if ip_str in seen:
                continue
            seen.add(ip_str)
            try:
                ip_obj = ipaddress.ip_address(ip_str)
            except ValueError:
                return False, f"DNS returned invalid IP: {ip_str}"
            ok, err = _check_ip(ip_obj)
            if not ok:
                return False, err

        return True, ""

    except Exception as e:
        return False, f"URL validation error: {e}"


def _strip_html(html: str) -> Tuple[str, Optional[str]]:
    """Return (extracted_text, page_title) from HTML."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")

    page_title = None
    if soup.title and soup.title.string:
        page_title = soup.title.string.strip()[:500] or None

    # Drop noise.
    for tag in soup(["script", "style", "noscript", "template", "iframe", "svg"]):
        tag.decompose()

    text = soup.get_text(separator="\n")
    # Collapse whitespace.
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    cleaned = "\n".join(lines)
    return cleaned, page_title


def fetch_and_extract(url: str) -> Tuple[str, Optional[str]]:
    """Fetch URL with SSRF-safe redirect handling and extract visible text.

    Raises ValueError on policy violations (SSRF, content type, size cap).
    Returns (text, page_title).
    """
    current = url
    with httpx.Client(
        timeout=FETCH_TIMEOUT,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*;q=0.5"},
        follow_redirects=False,
    ) as client:
        for _ in range(MAX_REDIRECTS):
            ok, err = is_safe_url(current)
            if not ok:
                raise ValueError(f"URL blocked: {err}")

            try:
                r = client.get(current)
            except httpx.HTTPError as e:
                raise ValueError(f"fetch failed: {e}") from e

            if r.is_redirect:
                loc = r.headers.get("Location")
                if not loc:
                    break
                current = str(httpx.URL(current).join(loc))
                continue

            r.raise_for_status()
            content_type = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if content_type and not (
                content_type.startswith("text/")
                or content_type in {"application/xhtml+xml", "application/xml"}
            ):
                raise ValueError(f"unsupported content-type: {content_type}")

            if len(r.content) > MAX_BYTES:
                raise ValueError(
                    f"page too large (>{MAX_BYTES // (1024*1024)} MB)"
                )

            html = r.text
            text, page_title = _strip_html(html)
            if not text or len(text) < 30:
                raise ValueError("no meaningful text extracted from page")
            return text, page_title

        raise ValueError("too many redirects")
