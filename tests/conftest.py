"""Shared test setup.

Runs `async def` tests with `asyncio.run`, so the suite needs only pytest.

The suite is offline: it never reads the developer's .env, never sees a
real key, and a connection to anything but this machine fails the test (the
web player and the fake servers the tests start listen on loopback).
"""

import asyncio
import inspect
import ipaddress
import os
import socket

import dotenv
import pytest

# Before any project module is imported: main.py loads .env at import, and
# a developer's real keys must never enter the test process.
dotenv.load_dotenv = lambda *args, **kwargs: False

ENV_VARS = (
    "OPENAI_API_KEY", "OPENAI_BASE_URL", "LTXV_SERVER_URL", "LTX2_API_KEY",
    "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST",
)
for _var in ENV_VARS:
    os.environ.pop(_var, None)


def pytest_pyfunc_call(pyfuncitem):
    if inspect.iscoroutinefunction(pyfuncitem.obj):
        args = {name: pyfuncitem.funcargs[name] for name in pyfuncitem._fixtureinfo.argnames}
        asyncio.run(pyfuncitem.obj(**args))
        return True
    return None


class NetworkBlocked(RuntimeError):
    pass


def _loopback(host) -> bool:
    if host in ("localhost", "", None):
        return True
    try:
        return ipaddress.ip_address(str(host).split("%")[0]).is_loopback
    except ValueError:
        return False


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """Fail loudly if anything tries to leave this machine."""
    connect, connect_ex, getaddrinfo = socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo

    def guard(address):
        if isinstance(address, tuple) and not _loopback(address[0]):
            raise NetworkBlocked(f"tests must not use the network (tried {address[0]})")

    def guarded_connect(self, address):
        guard(address)
        return connect(self, address)

    def guarded_connect_ex(self, address):
        guard(address)
        return connect_ex(self, address)

    def guarded_getaddrinfo(host, *args, **kwargs):
        guard((host,))
        return getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Each test starts with no keys and no address overrides."""
    for var in ENV_VARS:
        monkeypatch.delenv(var, raising=False)
