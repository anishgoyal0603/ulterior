"""
A tiny local HTTP proxy that every crawling browser is forced through.

WHY: the navigation guard checks a URL by resolving its host in Python, then
lets Chromium continue -- and Chromium resolves the host AGAIN, on its own.
A hostile DNS server with a zero TTL can answer the first lookup with a
public address and the second with 169.254.169.254 or 127.0.0.1 (DNS
rebinding). Checking the name is not enough; the check has to happen on the
address actually connected to.

This proxy is that place. Chromium sends every request to it (HTTP as
absolute-form requests, HTTPS and WebSockets as CONNECT tunnels). For each
one the proxy resolves the host ONCE, refuses if any address is private,
loopback, link-local, carrier-grade NAT, NAT64, ... (the same rules as
app/ssrf_guard.py), and then connects to that exact validated address. There
is no second lookup to rebind. With all traffic proxied, Chromium's
"disable_non_proxied_udp" WebRTC policy also leaves WebRTC no route out.

Scope: it listens on 127.0.0.1 only, on a random port, for this process's
own browsers. Chromium always connects to loopback names (localhost,
127.0.0.1, [::1]) directly; those are still checked by the navigation guard,
and in production loopback is refused there.

Off switch: EGRESS_PROXY=off (for diagnosing a site that will not load).
"""
import asyncio
import ipaddress
import logging
import socket
import threading
from typing import Callable, List, Optional
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

_MAX_HEAD = 64 * 1024
_IDLE_SECONDS = 120


def _default_resolver(host: str, port: int) -> List[str]:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(info[4][0] for info in infos))


def _default_ip_allowed(ip: str) -> bool:
    from app.ssrf_guard import _is_private_or_reserved
    return not _is_private_or_reserved(ip)


def _default_trusted() -> set:
    """host:port of this server's own public address (PUBLIC_BASE_URL). The
    hosted practice storefront lives there; in development that is
    127.0.0.1:8000, which the address rules would otherwise refuse. Set by
    server configuration only."""
    from urllib.parse import urlsplit as _split
    from app import config
    u = _split(config.PUBLIC_BASE_URL)
    if not u.hostname:
        return set()
    port = u.port or (443 if u.scheme == "https" else 80)
    return {f"{u.hostname.lower()}:{port}"}


class EgressProxy:
    def __init__(self, resolver: Optional[Callable[[str, int], List[str]]] = None,
                 ip_allowed: Optional[Callable[[str], bool]] = None,
                 trusted_hostports: Optional[set] = None):
        self.resolver = resolver or _default_resolver
        self.ip_allowed = ip_allowed or _default_ip_allowed
        self.trusted = trusted_hostports if trusted_hostports is not None else (
            _default_trusted() if ip_allowed is None else set())
        self.port: Optional[int] = None
        self.refused: List[str] = []
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._ready = threading.Event()
        self._error: Optional[BaseException] = None

    # ---------------------------------------------------------------- life
    def start(self) -> int:
        t = threading.Thread(target=self._run, name="egress-proxy", daemon=True)
        t.start()
        if not self._ready.wait(10) or self._error or not self.port:
            raise RuntimeError(f"egress proxy did not start: {self._error!r}")
        return self.port

    def _run(self) -> None:
        try:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            server = self._loop.run_until_complete(
                asyncio.start_server(self._handle, host="127.0.0.1", port=0))
            self.port = server.sockets[0].getsockname()[1]
        except BaseException as exc:      # noqa: BLE001 -- reported to start()
            self._error = exc
            self._ready.set()
            return
        self._ready.set()
        self._loop.run_forever()

    # ---------------------------------------------------------------- policy
    async def _pick_address(self, host: str, port: int) -> Optional[str]:
        host = host.strip("[]")
        trusted = f"{host.lower()}:{port}" in self.trusted
        try:
            ipaddress.ip_address(host.split("%", 1)[0])
            candidates = [host]
        except ValueError:
            try:
                candidates = await asyncio.get_running_loop().run_in_executor(
                    None, self.resolver, host, port)
            except Exception:
                return None
        if not candidates:
            return None
        # Every address must be allowed, not just the first: a name that
        # resolves to one public and one internal address is refused.
        if not trusted and not all(self.ip_allowed(ip) for ip in candidates):
            return None
        return candidates[0]

    # ---------------------------------------------------------------- traffic
    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        upstream_writer = None
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=30)
            if len(head) > _MAX_HEAD:
                return await self._reply(writer, 431, "Request header too large")
            lines = head.decode("latin-1").split("\r\n")
            method, target, version = (lines[0].split(" ", 2) + ["", ""])[:3]
            if method.upper() == "CONNECT":
                host, _, port_s = target.rpartition(":")
                port = int(port_s or 443)
                rest = b""
            else:
                url = urlsplit(target)
                if url.scheme not in ("http",) or not url.hostname:
                    return await self._reply(writer, 400, "Absolute http URL required")
                host, port = url.hostname, url.port or 80
                path = (url.path or "/") + (f"?{url.query}" if url.query else "")
                kept = [ln for ln in lines[1:] if ln and not ln.lower().startswith(
                    ("proxy-connection:", "proxy-authorization:", "connection:", "keep-alive:"))]
                # One request per upstream connection: a reused proxy
                # connection could otherwise carry a request for a different
                # host down this already-validated tunnel.
                rest = ("\r\n".join([f"{method} {path} {version}"] + kept + ["Connection: close", "", ""])
                        ).encode("latin-1")

            address = await self._pick_address(host, port)
            if address is None:
                note = f"{host}:{port}"
                if note not in self.refused:
                    self.refused.append(note)
                return await self._reply(writer, 403, "Blocked by audit network policy")

            up_reader, upstream_writer = await asyncio.wait_for(
                asyncio.open_connection(address, port), timeout=15)
            if method.upper() == "CONNECT":
                writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await writer.drain()
            else:
                upstream_writer.write(rest)
                await upstream_writer.drain()
            await asyncio.gather(self._pipe(reader, upstream_writer), self._pipe(up_reader, writer))
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError,
                ConnectionError, OSError, ValueError):
            pass
        finally:
            for w in (upstream_writer, writer):
                if w is not None:
                    try:
                        w.close()
                    except Exception:
                        pass

    @staticmethod
    async def _pipe(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
        try:
            while True:
                data = await asyncio.wait_for(src.read(65536), timeout=_IDLE_SECONDS)
                if not data:
                    break
                dst.write(data)
                await dst.drain()
        except Exception:
            pass
        finally:
            try:
                dst.close()
            except Exception:
                pass

    @staticmethod
    async def _reply(writer: asyncio.StreamWriter, code: int, text: str) -> None:
        body = text.encode()
        writer.write(f"HTTP/1.1 {code} {text}\r\nContent-Type: text/plain\r\n"
                     f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body)
        try:
            await writer.drain()
        except Exception:
            pass


_shared: Optional[EgressProxy] = None
_shared_lock = threading.Lock()


def shared_proxy() -> EgressProxy:
    """One proxy per process, started on first use."""
    global _shared
    with _shared_lock:
        if _shared is None:
            proxy = EgressProxy()
            proxy.start()
            _shared = proxy
        return _shared
