"""Client for the endpoints of the local API, version `api` 1."""

import asyncio
import contextlib
import json
from collections.abc import Callable, Iterator
from typing import Any

import aiohttp

from .exceptions import (
    ApiUnsupported,
    CannotConnect,
    GridyardError,
    SessionLost,
    device_error,
)
from .models import Catalog, DeviceInfo, Profile, State
from .session import REQUEST_TIMEOUT_S, DeviceSession

API = 1
# The device restarts 1 s after profile or meter-key; the client then reads
# device-info every 2 s for at most 60 s (contract, profile and meter-key).
RESTART_POLL_INTERVAL_S = 2.0
RESTART_TIMEOUT_S = 60.0
# A catalog that changes while it is read is read again from the start.
CATALOG_ATTEMPTS = 3


class GridyardClient:
    """Read and configure one device over its local API."""

    def __init__(
        self,
        websession: aiohttp.ClientSession,
        host: str,
        setup_code: str,
        *,
        port: int = 80,
        timeout: float = REQUEST_TIMEOUT_S,
    ) -> None:
        """Prepare the client; see DeviceSession for the websession."""
        self._session = DeviceSession(
            websession, host, setup_code, port=port, timeout=timeout
        )

    async def connect(self) -> None:
        """Set up the session: checks reachability, setup code and API version."""
        await self._session.connect()

    async def device_info(self) -> DeviceInfo:
        """Read device-info."""
        response = await self._json("device-info", {})
        with _parsing("device-info"):
            return DeviceInfo.from_json(response)

    async def state(self) -> State:
        """Read the latest record and the device state."""
        response = await self._json("state", {})
        with _parsing("state"):
            return State.from_json(response)

    async def profiles(self) -> Catalog:
        """Read the whole profile catalog page by page."""
        for _ in range(CATALOG_ATTEMPTS):
            catalog = await self._read_catalog()
            if catalog is not None:
                return catalog
        msg = f"profiles: catalog changed while reading, {CATALOG_ATTEMPTS} times"
        raise GridyardError(msg)

    async def set_profile(self, profile_id: str) -> None:
        """Select the meter profile and wait until the device has restarted with it.

        A lost response is not repeated: the device may have applied the profile,
        and device-info shows whether it did.
        """
        with contextlib.suppress(SessionLost):
            await self._text("profile", profile_id)
        await self._wait_for_device(lambda info: info.profile_id == profile_id)

    async def set_meter_key(self, key: str) -> None:
        """Set the meter key (32 hex characters) and wait until the device answers.

        The key is not shown by the device, so a lost response raises SessionLost;
        sending the same key again is harmless.
        """
        await self._text("meter-key", key)
        await self._wait_for_device(lambda _info: True)

    async def _read_catalog(self) -> Catalog | None:
        """Read all pages; None if the catalog changed in between."""
        catalog = None
        profiles: list[Profile] = []
        while True:
            page = await self._json("profiles", {"offset": len(profiles)})
            with _parsing("profiles"):
                page_catalog = page["catalog"]
                total = page["total"]
                entries = [Profile.from_json(entry) for entry in page["profiles"]]
            if catalog is None:
                catalog = page_catalog
            if page_catalog != catalog:
                return None
            profiles.extend(entries)
            if len(profiles) >= total:
                return Catalog(catalog=catalog, profiles=tuple(profiles))
            if not entries:
                msg = f"profiles: empty page at offset {len(profiles)} of {total}"
                raise GridyardError(msg)

    async def _wait_for_device(self, done: Callable[[DeviceInfo], bool]) -> None:
        """Read device-info until done() holds; the device is away while it restarts."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + RESTART_TIMEOUT_S
        while loop.time() < deadline:
            await asyncio.sleep(RESTART_POLL_INTERVAL_S)
            try:
                info = await self.device_info()
            except CannotConnect, SessionLost:
                continue
            if done(info):
                return
        msg = f"device-info: expected state not reached within {RESTART_TIMEOUT_S} s"
        raise GridyardError(msg)

    async def _json(self, endpoint: str, request: dict[str, Any]) -> dict[str, Any]:
        """Send a JSON request and return the response, or raise its error."""
        body = json.dumps({"api": API, **request}, separators=(",", ":")).encode()
        raw = await self._session.request(endpoint, body)
        try:
            response = json.loads(raw)
        except ValueError as err:
            msg = f"{endpoint}: response is not JSON"
            raise GridyardError(msg) from err
        if not isinstance(response, dict):
            msg = f"{endpoint}: response is not a JSON object"
            raise GridyardError(msg)
        if response.get("error"):
            raise device_error(
                endpoint,
                response["error"],
                retryable=response.get("retryable", False),
                retry_after_s=response.get("retry_after_s"),
            )
        if response.get("api") != API:
            msg = f"{endpoint}: local API {response.get('api')}, expected {API}"
            raise ApiUnsupported(msg)
        return response

    async def _text(self, endpoint: str, value: str) -> None:
        """Send a value to a text endpoint, which answers OK or an error code."""
        raw = await self._session.request(endpoint, value.encode(), idempotent=False)
        answer = raw.decode(errors="replace").strip()
        if answer != "OK":
            raise device_error(endpoint, answer)


@contextlib.contextmanager
def _parsing(endpoint: str) -> Iterator[None]:
    """Turn a missing field or a wrong type in a response into GridyardError."""
    try:
        yield
    except (KeyError, TypeError, ValueError, ArithmeticError) as err:
        msg = f"{endpoint}: unexpected response: {err!r}"
        raise GridyardError(msg) from err
