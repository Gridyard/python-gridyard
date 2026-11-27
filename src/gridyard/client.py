"""Client for the endpoints of the local API, version `api` 1."""

import asyncio
import contextlib
import hashlib
import json
from collections.abc import AsyncIterator, Callable
from typing import Any

import aiohttp

from .exceptions import (
    ApiUnsupported,
    CannotConnect,
    GridyardError,
    SessionLost,
    UpdateFailed,
    device_error,
    invalid_response,
)
from .models import Catalog, DeviceInfo, Profile, State
from .session import REQUEST_TIMEOUT_S, DeviceSession

API = 1
# The device restarts 1 s after profile or meter-key; the client then polls every
# 2 s for at most 60 s (contract, profile and meter-key).
RESTART_POLL_INTERVAL_S = 2.0
RESTART_TIMEOUT_S = 60.0
# Upload of an image: blocks for the progress, time limit for the whole upload.
UPLOAD_BLOCK_SIZE = 16384
UPLOAD_TIMEOUT_S = 300.0
# After the upload: restart, trial boot (60 s) and confirmation, at most 3 min.
UPDATE_CONFIRM_TIMEOUT_S = 180.0
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
        with invalid_response("device-info"):
            return DeviceInfo.from_json(response)

    async def state(self) -> State:
        """Read the latest record and the device state."""
        response = await self._json("state", {})
        with invalid_response("state"):
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
        """Select the meter profile; return once the device restarted with it."""
        info = await self._send_and_wait_for_restart("profile", profile_id)
        if info.profile_id != profile_id:
            msg = f"profile: device restarted with {info.profile_id}, not {profile_id}"
            raise GridyardError(msg)

    async def set_meter_key(self, key: str) -> None:
        """Set the meter key (32 hex characters); return once the device restarted."""
        await self._send_and_wait_for_restart("meter-key", key)

    async def update(
        self,
        version: str,
        image: bytes,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> None:
        """Install a firmware image; return once the device runs it and confirmed it.

        The image goes to the device only after update-begin accepted version, size
        and SHA-256. on_progress gets bytes sent and total during the upload. After
        the upload the device restarts into a trial boot; it confirms the new
        version (axis update idle) or falls back (update failed, UpdateFailed).
        """
        boot_id = (await self.state()).boot_id
        offer = {
            "version": version,
            "size": len(image),
            "sha256": hashlib.sha256(image).hexdigest(),
        }
        response = await self._json("update-begin", offer, idempotent=False)
        with invalid_response("update-begin"):
            token = response["token"]
        headers = {
            "X-Update-Token": token,
            "Content-Type": "application/octet-stream",
            "Content-Length": str(len(image)),
        }
        _status, raw = await self._session.post_plain(
            "update", _chunks(image, on_progress), headers, UPLOAD_TIMEOUT_S
        )
        _json_response("update", raw)
        await self._wait_for_update(boot_id, version)

    async def _wait_for_update(self, boot_id: str, version: str) -> None:
        """Wait until the device restarted and confirmed version, or fell back."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + UPDATE_CONFIRM_TIMEOUT_S
        while loop.time() < deadline:
            await asyncio.sleep(RESTART_POLL_INTERVAL_S)
            try:
                state = await self.state()
            except CannotConnect, SessionLost:
                continue
            if state.boot_id == boot_id:
                continue
            update = state.axes["update"]
            if update.state == "failed":
                msg = f"update: device fell back to {state.firmware} ({update.error})"
                raise UpdateFailed(msg)
            if update.state == "idle" and state.firmware == version:
                return
        msg = f"update: {version} not confirmed within {UPDATE_CONFIRM_TIMEOUT_S} s"
        raise GridyardError(msg)

    async def _send_and_wait_for_restart(self, endpoint: str, value: str) -> DeviceInfo:
        """Send a value, wait for the restart it causes and return device-info after it.

        The device restarts 1 s after its answer. A new boot_id in state shows the
        restart; device-info answers before it would still show the old state. A
        lost answer is not sent again, since the device may have taken the value;
        the restart shows that it did.
        """
        boot_id = (await self.state()).boot_id
        with contextlib.suppress(SessionLost):
            await self._text(endpoint, value)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + RESTART_TIMEOUT_S
        while loop.time() < deadline:
            await asyncio.sleep(RESTART_POLL_INTERVAL_S)
            try:
                state = await self.state()
            except CannotConnect, SessionLost:
                continue
            if state.boot_id != boot_id:
                return await self.device_info()
        msg = f"{endpoint}: device did not restart within {RESTART_TIMEOUT_S} s"
        raise GridyardError(msg)

    async def _read_catalog(self) -> Catalog | None:
        """Read all pages; None if the catalog changed in between."""
        catalog = None
        profiles: list[Profile] = []
        while True:
            page = await self._json("profiles", {"offset": len(profiles)})
            with invalid_response("profiles"):
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

    async def _json(
        self, endpoint: str, request: dict[str, Any], *, idempotent: bool = True
    ) -> dict[str, Any]:
        """Send a JSON request and return the response, or raise its error."""
        body = json.dumps({"api": API, **request}, separators=(",", ":")).encode()
        raw = await self._session.request(endpoint, body, idempotent=idempotent)
        return _json_response(endpoint, raw)

    async def _text(self, endpoint: str, value: str) -> None:
        """Send a value to a text endpoint, which answers OK or an error code."""
        raw = await self._session.request(endpoint, value.encode(), idempotent=False)
        answer = raw.decode(errors="replace").strip()
        if answer != "OK":
            raise device_error(endpoint, answer)


def _json_response(endpoint: str, raw: bytes) -> dict[str, Any]:
    """Return a JSON response of the local API, or raise its error."""
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


async def _chunks(
    image: bytes, on_progress: Callable[[int, int], None] | None
) -> AsyncIterator[bytes]:
    """Yield the image in blocks and report the bytes handed to the connection."""
    for start in range(0, len(image), UPLOAD_BLOCK_SIZE):
        yield image[start : start + UPLOAD_BLOCK_SIZE]
        if on_progress is not None:
            on_progress(min(start + UPLOAD_BLOCK_SIZE, len(image)), len(image))
