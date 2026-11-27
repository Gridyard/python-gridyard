"""Fixtures: a fake device on a local test server and a client session for it."""

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

import aiohttp
import pytest
from aiohttp.test_utils import TestServer

from gridyard import DeviceSession

from .device import FakeDevice

SETUP_CODE = "AAAABBBBCCCC"
TIMEOUT_S = 0.5
FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict[str, Any]:
    """Return a contract example from tests/fixtures as a dict."""
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture
def device() -> FakeDevice:
    """Return a fake device with the test setup code."""
    return FakeDevice(SETUP_CODE)


@pytest.fixture
async def server(
    device: FakeDevice, aiohttp_server: Callable[..., Awaitable[TestServer]]
) -> TestServer:
    """Serve the fake device on 127.0.0.1."""
    return await aiohttp_server(device.app())


@pytest.fixture
async def websession() -> AsyncIterator[aiohttp.ClientSession]:
    """Return a client session without a cookie jar."""
    async with aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar()) as websession:
        yield websession


@pytest.fixture
def make_session(
    server: TestServer, websession: aiohttp.ClientSession
) -> Callable[..., DeviceSession]:
    """Return a factory for sessions with the fake device."""

    def make(setup_code: str = SETUP_CODE) -> DeviceSession:
        return DeviceSession(
            websession, server.host, setup_code, port=server.port, timeout=TIMEOUT_S
        )

    return make
