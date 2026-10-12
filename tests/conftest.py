"""Offline test harness; only the configured test ledger host may use sockets."""

import os
import socket
from typing import Any
from urllib.parse import urlsplit

import pytest


@pytest.fixture(autouse=True)
def fake_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NVIDIA_API_KEY", "specvania-test-key-never-use-in-production")


@pytest.fixture(autouse=True)
def socket_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail before DNS or transport, except for the explicitly configured ledger."""
    ledger_url = os.environ.get("SPECVANIA_TEST_DATABASE_URL", "")
    ledger_host = urlsplit(ledger_url).hostname
    allowed_hosts = {ledger_host} if ledger_host else set()
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_sendto = socket.socket.sendto
    original_getaddrinfo = socket.getaddrinfo
    original_create_connection = socket.create_connection

    def check_host(host: Any) -> None:
        if isinstance(host, bytes):
            host = host.decode("ascii")
        if host not in allowed_hosts:
            pytest.fail(
                f"Network access blocked by socket guard: {host}", pytrace=False
            )

    def guarded_connect(sock: socket.socket, address: Any) -> None:
        if sock.family != socket.AF_UNIX:
            check_host(address[0])
        original_connect(sock, address)

    def guarded_connect_ex(sock: socket.socket, address: Any) -> int:
        if sock.family != socket.AF_UNIX:
            check_host(address[0])
        return original_connect_ex(sock, address)

    def guarded_sendto(sock: socket.socket, data: Any, *args: Any) -> int:
        if sock.family != socket.AF_UNIX:
            check_host(args[-1][0])
        return original_sendto(sock, data, *args)

    def guarded_getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
        check_host(host)
        addresses = original_getaddrinfo(host, *args, **kwargs)
        # create_connection connects to resolved IPs; trust only this host's results.
        for address in addresses:
            resolved_host = address[4][0]
            if isinstance(resolved_host, str):
                allowed_hosts.add(resolved_host)
        return addresses

    def guarded_create_connection(address: Any, *args: Any, **kwargs: Any) -> Any:
        check_host(address[0])
        return original_create_connection(address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)
    monkeypatch.setattr(socket.socket, "sendto", guarded_sendto)
    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)
    monkeypatch.setattr(socket, "create_connection", guarded_create_connection)
