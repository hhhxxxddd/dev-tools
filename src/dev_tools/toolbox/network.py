"""Bounded, direct DNS/TCP/TLS/HTTP diagnostics without executing project code."""

from __future__ import annotations

import http.client
import json
import os
import socket
import ssl
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from .platforms import native


@dataclass(frozen=True)
class NetworkTarget:
    raw: str
    host: str
    port: int
    scheme: str
    path: str
    display: str


def parse_target(raw: str) -> NetworkTarget:
    if not raw or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in raw):
        raise ValueError
    has_scheme = "://" in raw
    url = urlsplit(raw if has_scheme else "//" + raw)
    if (
        has_scheme
        and url.scheme not in {"http", "https"}
        or not url.hostname
        or url.username is not None
        or url.password is not None
        or url.fragment
        or not has_scheme
        and (url.path or url.query)
    ):
        raise ValueError
    port = url.port
    if port is None and has_scheme:
        port = {"http": 80, "https": 443}.get(url.scheme)
    if port is None or not 1 <= port <= 65535:
        raise ValueError
    host = url.hostname.encode("idna").decode("ascii")
    path = url.path or "/"
    if url.query:
        path += "?" + url.query
    # Queries may contain credentials. Send them, but never echo them in diagnostics.
    display = urlunsplit((url.scheme, url.netloc, url.path, "", "")) if has_scheme else raw
    return NetworkTarget(raw, host, port, url.scheme, path, display)


def proxy_info(environment: dict | None = None) -> list:
    environment = os.environ if environment is None else environment
    proxies = []
    seen = set()
    for key in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        if not (raw := environment.get(key)):
            continue
        canonical = key.lower() if os.name == "nt" else key
        if canonical in seen:
            continue
        seen.add(canonical)
        try:
            url = urlsplit(raw if "://" in raw else "http://" + raw)
            if not url.hostname:
                raise ValueError
            proxies.append(
                {
                    "variable": key,
                    "scheme": url.scheme,
                    "host": url.hostname,
                    "port": url.port,
                    "credentials": url.username is not None,
                    "valid": True,
                }
            )
        except ValueError:
            proxies.append({"variable": key, "valid": False})
    return proxies


def collect_info() -> dict:
    value = native().network_info()
    value.setdefault("warnings", [])
    return {
        **value,
        "hostname": socket.gethostname(),
        "proxies": proxy_info(),
        "proxy_scope": "environment",
        "complete": not value["warnings"],
    }


def resolve(host: str, port: int, timeout: int) -> list:
    # getaddrinfo has no portable timeout. A short isolated Python child makes DNS
    # bounded and cancellable, without leaving resolver threads alive on CLI exit.
    script = (
        "import json,socket,sys; "
        "print(json.dumps(socket.getaddrinfo(sys.argv[1],int(sys.argv[2]),"
        "type=socket.SOCK_STREAM)))"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c", script, host, str(port)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode:
        raise socket.gaierror
    records = json.loads(result.stdout)
    return list(
        dict.fromkeys(
            (record[0], tuple(record[4]))
            for record in records
            if record[0] in (socket.AF_INET, socket.AF_INET6)
        )
    )[:16]


def connect(addresses: list, timeout: int):
    deadline = time.monotonic() + timeout
    last = OSError()
    for family, address in addresses:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        stream = socket.socket(family, socket.SOCK_STREAM)
        try:
            stream.settimeout(remaining)
            stream.connect(address)
            return stream
        except OSError as exc:
            last = exc
            stream.close()
    raise last


def request_http(connection, stream, path: str, timeout: int) -> int:
    # A socket timeout alone resets on each read. Bound the whole response even
    # when a peer sends a slow stream of headers without finishing them.
    expired = threading.Event()

    def interrupt():
        expired.set()
        try:
            stream.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    timer = threading.Timer(timeout, interrupt)
    timer.daemon = True
    timer.start()
    try:
        stream.settimeout(timeout)
        connection.request("HEAD", path, headers={"Connection": "close"})
        response = connection.getresponse()
        status = response.status
        response.close()
        if expired.is_set():
            raise TimeoutError
        return status
    except OSError, http.client.HTTPException:
        if expired.is_set():
            raise TimeoutError from None
        raise
    finally:
        timer.cancel()


def check_target(target: NetworkTarget, timeout: int) -> dict:
    stages = []
    value = {
        "target": target.display,
        "host": target.host,
        "port": target.port,
        "connection_mode": "direct",
        "timeout": timeout,
        "proxies": proxy_info(),
        "stages": stages,
        "healthy": False,
        "complete": True,
        "warnings": [],
    }
    stream = connection = None

    def stage(name, operation):
        started = time.monotonic()
        try:
            result, details = operation()
        except (OSError, ValueError, subprocess.SubprocessError, http.client.HTTPException) as exc:
            code = (
                "timeout"
                if isinstance(exc, (TimeoutError, subprocess.TimeoutExpired))
                else "certificate_invalid"
                if isinstance(exc, ssl.SSLCertVerificationError)
                else "connection_refused"
                if isinstance(exc, ConnectionRefusedError)
                else "probe_failed"
            )
            stages.append(
                {
                    "stage": name,
                    "status": "failed",
                    "code": code,
                    "duration_ms": round((time.monotonic() - started) * 1000),
                }
            )
            return None, False
        stages.append(
            {
                "stage": name,
                "status": "ok",
                "code": "ok",
                "duration_ms": round((time.monotonic() - started) * 1000),
                **details,
            }
        )
        return result, True

    def dns():
        addresses = resolve(target.host, target.port, timeout)
        if not addresses:
            raise socket.gaierror
        return addresses, {"addresses": list(dict.fromkeys(address[0] for _, address in addresses))}

    try:
        addresses, ok = stage("dns", dns)
        if not ok:
            return value

        def tcp():
            sock = connect(addresses, timeout)
            return sock, {"peer": sock.getpeername()[0]}

        stream, ok = stage("tcp", tcp)
        if not ok:
            return value
        if target.scheme == "https":

            def tls():
                stream.settimeout(timeout)
                wrapped = ssl.create_default_context().wrap_socket(
                    stream, server_hostname=target.host
                )
                return wrapped, {"version": wrapped.version()}

            wrapped, ok = stage("tls", tls)
            if not ok:
                return value
            stream = wrapped
        if target.scheme in {"http", "https"}:
            connection = http.client.HTTPConnection(target.host, target.port, timeout=timeout)
            connection.sock = stream

            def http_probe():
                status = request_http(connection, stream, target.path, timeout)
                return status, {"http_status": status}

            status, ok = stage("http", http_probe)
            if not ok:
                return value
            if status >= 400:
                stages[-1].update(status="failed", code="http_status")
                return value
        value["healthy"] = True
        return value
    finally:
        if connection is not None:
            connection.close()
        if stream is not None:
            stream.close()
