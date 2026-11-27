"""Test the encrypted session against the fake device."""

from collections.abc import Callable

import aiohttp
import pytest

from gridyard import (
    CannotConnect,
    DeviceSession,
    GridyardError,
    InvalidAuth,
    SessionLost,
)

from .conftest import SETUP_CODE, TIMEOUT_S
from .device import FakeDevice

DATA = b'{"api":1}'


async def test_request_is_encrypted(
    device: FakeDevice, make_session: Callable[..., DeviceSession]
) -> None:
    """Test a request is encrypted on the way and the response comes back decrypted."""
    session = make_session()

    assert await session.request("echo", DATA) == DATA
    assert await session.request("echo", b"second") == b"second"
    assert device.applied == [("echo", DATA), ("echo", b"second")]
    assert DATA not in device.last_raw_request


async def test_wrong_setup_code(make_session: Callable[..., DeviceSession]) -> None:
    """Test the device rejects a wrong setup code."""
    session = make_session("AAAABBBBCCCD")

    with pytest.raises(InvalidAuth):
        await session.connect()


async def test_session_taken_over(
    device: FakeDevice, make_session: Callable[..., DeviceSession]
) -> None:
    """Test a client sets up the session again after another client took it over."""
    first = make_session()
    second = make_session()
    await first.connect()
    await second.connect()

    assert await first.request("echo", DATA) == DATA
    assert device.applied == [("echo", DATA)]


async def test_lost_response_repeats_idempotent_request(
    device: FakeDevice, make_session: Callable[..., DeviceSession]
) -> None:
    """Test a lost response leads to a new session and one repeat of the request."""
    session = make_session()
    await session.connect()
    device.lost_responses = 1

    assert await session.request("echo", DATA) == DATA
    assert device.applied == [("echo", DATA), ("echo", DATA)]


async def test_timeout_repeats_idempotent_request(
    device: FakeDevice, make_session: Callable[..., DeviceSession]
) -> None:
    """Test a response after the time limit leads to a new session and one repeat."""
    session = make_session()
    await session.connect()
    device.slow_responses = 1
    device.slow_response_s = TIMEOUT_S * 2

    assert await session.request("echo", DATA) == DATA
    assert device.applied == [("echo", DATA), ("echo", DATA)]


async def test_lost_response_twice(
    device: FakeDevice, make_session: Callable[..., DeviceSession]
) -> None:
    """Test the request fails with SessionLost when the repeat fails too."""
    session = make_session()
    await session.connect()
    device.lost_responses = 2

    with pytest.raises(SessionLost):
        await session.request("echo", DATA)
    assert await session.request("echo", DATA) == DATA


async def test_lost_response_does_not_repeat_change(
    device: FakeDevice, make_session: Callable[..., DeviceSession]
) -> None:
    """Test a request that changes the device is not repeated after a lost response."""
    session = make_session()
    await session.connect()
    device.lost_responses = 1

    with pytest.raises(SessionLost):
        await session.request("echo", DATA, idempotent=False)
    assert device.applied == [("echo", DATA)]
    assert await session.request("echo", b"check") == b"check"


async def test_cannot_connect(
    websession: aiohttp.ClientSession, unused_tcp_port: int
) -> None:
    """Test a device that does not answer."""
    session = DeviceSession(
        websession, "127.0.0.1", SETUP_CODE, port=unused_tcp_port, timeout=TIMEOUT_S
    )

    with pytest.raises(CannotConnect):
        await session.connect()


async def test_cookie_jar_rejected() -> None:
    """Test a client session with a cookie jar is refused."""
    async with aiohttp.ClientSession() as websession:
        with pytest.raises(TypeError, match="DummyCookieJar"):
            DeviceSession(websession, "127.0.0.1", SETUP_CODE)


@pytest.mark.parametrize(
    "proto_ver",
    [
        b"not json",
        b"[]",
        b'{"gridyard":{"ver":"1"}}',
        b'{"gridyard":{"ver":"1"},"prov":{}}',
    ],
    ids=["not-json", "not-object", "no-prov", "no-sec-ver"],
)
async def test_malformed_proto_ver(
    device: FakeDevice, make_session: Callable[..., DeviceSession], proto_ver: bytes
) -> None:
    """Test a malformed proto-ver response is a GridyardError."""
    device.proto_ver = proto_ver

    with pytest.raises(GridyardError, match="proto-ver"):
        await make_session().connect()
