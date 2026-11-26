"""Encrypted session with the local API of a gridyard device.

The device runs ESP-IDF protocomm with Security 2 (SRP6a and AES-GCM) over HTTP.
Every request is `POST /<endpoint>`; the first response sets the cookie `session`,
which the client sends with every later request.
"""

import asyncio
import json
from http import HTTPStatus

import aiohttp
from cryptography.exceptions import InvalidTag
from google.protobuf.message import DecodeError
from yarl import URL

from .exceptions import CannotConnect, GridyardError, InvalidAuth, SessionLost
from .security2 import Security2

USERNAME = "wifiprov"
SECURITY_SCHEME = 2
REQUEST_TIMEOUT_S = 10.0
# Headers of esp_prov's HTTP transport (network_provisioning 1.3.1,
# tool/esp_prov/transport/transport_http.py).
HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded",
    "Accept": "text/plain",
}


class _TransportError(Exception):
    """A request got no valid response; the session is no longer usable."""


class DeviceSession:
    """Encrypted session with one device.

    The device keeps a single session, and a new client takes it over. Encryption
    and decryption share one counter on both sides, so a request without a valid
    response leaves the session unusable. Requests are therefore sent one at a time,
    and after a failure the session is set up again.
    """

    def __init__(
        self,
        websession: aiohttp.ClientSession,
        host: str,
        setup_code: str,
        *,
        port: int = 80,
        timeout: float = REQUEST_TIMEOUT_S,
    ) -> None:
        """Prepare a session; nothing is sent before connect() or request().

        The websession must use aiohttp.DummyCookieJar: the session sends the device
        cookie itself, and a cookie jar would replace that header with a stored
        cookie (or store none for an IP address).
        """
        if not isinstance(websession.cookie_jar, aiohttp.DummyCookieJar):
            msg = "websession must use aiohttp.DummyCookieJar"
            raise TypeError(msg)
        self._websession = websession
        self._base_url = URL.build(scheme="http", host=host, port=port)
        self._setup_code = setup_code
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._lock = asyncio.Lock()
        self._cookie: str | None = None
        self._security: Security2 | None = None

    async def connect(self) -> None:
        """Set up a new session with the device."""
        async with self._lock:
            await self._connect()

    async def request(
        self, endpoint: str, data: bytes, *, idempotent: bool = True
    ) -> bytes:
        """Send data encrypted to an endpoint and return the decrypted response.

        After a failed request the session is set up again, and an idempotent
        request is sent once more. Any other request is not repeated, because the
        device may have applied it before the response got lost; SessionLost tells
        the caller to check the device state instead.
        """
        async with self._lock:
            if self._security is None:
                await self._connect()
            try:
                return await self._exchange(endpoint, data)
            except _TransportError as err:
                first_error = err
            await self._connect()
            if not idempotent:
                msg = (
                    f"{endpoint}: session lost, the device may have applied the request"
                )
                raise SessionLost(msg) from first_error
            try:
                return await self._exchange(endpoint, data)
            except _TransportError as err:
                msg = f"{endpoint}: session lost again after setting it up anew"
                raise SessionLost(msg) from err

    async def _connect(self) -> None:
        self._cookie = None
        self._security = None
        try:
            versions = await self._post("proto-ver", b"---")
        except _TransportError as err:
            raise CannotConnect(str(err)) from err
        security = Security2(
            _security_patch_version(versions), USERNAME, self._setup_code, verbose=False
        )

        # SRP6a takes tens of milliseconds on a laptop and longer on small hosts.
        command0 = await asyncio.to_thread(security.security_session, None)
        try:
            response0 = await self._post("prov-session", command0.encode("latin-1"))
        except _TransportError as err:
            raise CannotConnect(str(err)) from err

        try:
            command1 = await asyncio.to_thread(
                security.security_session, response0.decode("latin-1")
            )
        except (DecodeError, RuntimeError) as err:
            msg = f"prov-session: invalid response to the first step: {err}"
            raise GridyardError(msg) from err
        try:
            response1 = await self._post("prov-session", command1.encode("latin-1"))
        except _TransportError as err:
            # The device closes the connection without a response when the client
            # proof does not match.
            msg = f"device rejected the setup code: {err}"
            raise InvalidAuth(msg) from err

        try:
            await asyncio.to_thread(
                security.security_session, response1.decode("latin-1")
            )
        except (DecodeError, RuntimeError) as err:
            msg = f"device did not prove that it knows the setup code: {err}"
            raise InvalidAuth(msg) from err
        self._security = security

    async def _exchange(self, endpoint: str, data: bytes) -> bytes:
        if self._security is None:
            msg = "no session"
            raise _TransportError(msg)
        response = await self._post(endpoint, self._security.encrypt_data(data))
        try:
            return self._security.decrypt_data(response)
        except InvalidTag as err:
            msg = f"{endpoint}: response does not decrypt"
            raise _TransportError(msg) from err

    async def _post(self, endpoint: str, data: bytes) -> bytes:
        headers = dict(HEADERS)
        if self._cookie is not None:
            headers["Cookie"] = self._cookie
        url = self._base_url / endpoint
        try:
            async with self._websession.post(
                url, data=data, headers=headers, timeout=self._timeout
            ) as response:
                status = response.status
                set_cookie = response.headers.get("Set-Cookie")
                body = await response.read()
        except (aiohttp.ClientError, TimeoutError) as err:
            msg = f"{endpoint}: {type(err).__name__}: {err}"
            raise _TransportError(msg) from err
        if status != HTTPStatus.OK:
            msg = f"{endpoint}: HTTP {status}"
            raise _TransportError(msg)
        if set_cookie is not None:
            self._cookie = set_cookie.split(";", 1)[0]
        return body


def _security_patch_version(response: bytes) -> int:
    """Check the security scheme in a proto-ver response and return sec_patch_ver."""
    try:
        versions = json.loads(response)
    except ValueError as err:
        msg = f"proto-ver: response is not JSON: {response[:64]!r}"
        raise GridyardError(msg) from err
    prov = versions["prov"]
    if prov["sec_ver"] != SECURITY_SCHEME:
        msg = (
            f"device uses security scheme {prov['sec_ver']}, expected {SECURITY_SCHEME}"
        )
        raise GridyardError(msg)
    return prov["sec_patch_ver"]
