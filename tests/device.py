"""Device side of protocomm Security 2 over HTTP, for tests only.

Follows ESP-IDF v6.1:

- protocomm_httpd.c: a request without the cookie of the current session closes that
  session and opens a new one; the response that opens a session sets
  `Set-Cookie: session=<number>`. When a handler fails, the server closes the
  connection without a response.
- esp_srp.c: SRP6a server with SHA-512 and the 3072-bit group; K = SHA-512(S).
- security2.c: AES-256-GCM with the first 32 bytes of K. The 12-byte IV is 8 random
  bytes and a big-endian counter that starts at 1 and counts every encryption and
  decryption.
"""

import asyncio
import hashlib
import os
import random
from collections.abc import Callable
from pathlib import Path

from aiohttp import web
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from gridyard import srp6a
from gridyard.proto import constants_pb2, sec2_pb2, session_pb2

USERNAME = "wifiprov"
PROTO_VER = (
    Path(__file__).parent / "fixtures" / "proto-ver.local.response.json"
).read_bytes()
N, G = srp6a.get_ng(srp6a.NG_3072)
N_LEN = len(srp6a.long_to_bytes(N))
HASH = hashlib.sha512


class DeviceFailureError(Exception):
    """A handler failed; the server closes the connection without a response."""


class FakeDevice:
    """One device with one session, as an aiohttp application."""

    def __init__(self, setup_code: str) -> None:
        """Compute salt and verifier for the setup code, as the device does at start."""
        self.salt, verifier = srp6a.generate_salt_and_verifier(
            USERNAME, setup_code, len_s=16
        )
        self.verifier = srp6a.bytes_to_long(verifier)
        self.endpoints: dict[str, Callable[[bytes], bytes]] = {
            "echo": lambda data: data
        }
        self.applied: list[tuple[str, bytes]] = []
        self.last_raw_request = b""
        self.lost_responses = 0
        self.slow_responses = 0
        self.slow_response_s = 0.0
        self.session_id: int | None = None
        self._handshake: tuple[int, int, bytes] | None = None
        self._cipher: AESGCM | None = None
        self._iv = bytearray()

    def app(self) -> web.Application:
        """Return the HTTP server of the device."""
        app = web.Application()
        app.router.add_post("/{endpoint}", self._handle)
        return app

    async def _handle(self, request: web.Request) -> web.StreamResponse:
        endpoint = request.match_info["endpoint"]
        new_session = request.headers.get("Cookie") != f"session={self.session_id}"
        if new_session:
            self._open_session()
        body = await request.read()
        try:
            response_body = self._dispatch(endpoint, body)
        except DeviceFailureError:
            return _close_without_response(request)
        if endpoint in self.endpoints and self.lost_responses:
            self.lost_responses -= 1
            return _close_without_response(request)
        if endpoint in self.endpoints and self.slow_responses:
            self.slow_responses -= 1
            await asyncio.sleep(self.slow_response_s)
        response = web.Response(body=response_body)
        if new_session:
            response.headers["Set-Cookie"] = f"session={self.session_id}"
        return response

    def _open_session(self) -> None:
        self.session_id = random.getrandbits(32)
        self._handshake = None
        self._cipher = None

    def _dispatch(self, endpoint: str, body: bytes) -> bytes:
        if endpoint == "proto-ver":
            return PROTO_VER
        if endpoint == "prov-session":
            return self._session_step(body)
        if self._cipher is None or endpoint not in self.endpoints:
            raise DeviceFailureError
        self.last_raw_request = body
        try:
            data = self._cipher.decrypt(self._next_iv(), body, None)
        except InvalidTag as err:
            raise DeviceFailureError from err
        self.applied.append((endpoint, data))
        return self._cipher.encrypt(
            self._next_iv(), self.endpoints[endpoint](data), None
        )

    def _session_step(self, body: bytes) -> bytes:
        request = session_pb2.SessionData()
        request.ParseFromString(body)
        if request.sec2.msg == sec2_pb2.S2Session_Command0:
            return self._command0(request.sec2.sc0)
        if (
            request.sec2.msg == sec2_pb2.S2Session_Command1
            and self._handshake is not None
        ):
            return self._command1(request.sec2.sc1)
        raise DeviceFailureError

    def _command0(self, sc0: sec2_pb2.S2SessionCmd0) -> bytes:
        k = srp6a.H(HASH, N, G, width=N_LEN)
        b = srp6a.get_random_of_length(32)
        pubkey_b = (k * self.verifier + pow(G, b, N)) % N
        pubkey_a = srp6a.bytes_to_long(sc0.client_pubkey)
        u = srp6a.H(HASH, pubkey_a, pubkey_b, width=N_LEN)
        secret = pow(pubkey_a * pow(self.verifier, u, N), b, N)
        self._handshake = (
            pubkey_a,
            pubkey_b,
            HASH(srp6a.long_to_bytes(secret)).digest(),
        )

        response = session_pb2.SessionData(sec_ver=session_pb2.SecScheme2)
        response.sec2.msg = sec2_pb2.S2Session_Response0
        response.sec2.sr0.status = constants_pb2.Success
        response.sec2.sr0.device_pubkey = srp6a.long_to_bytes(pubkey_b)
        response.sec2.sr0.device_salt = self.salt
        return response.SerializeToString()

    def _command1(self, sc1: sec2_pb2.S2SessionCmd1) -> bytes:
        pubkey_a, pubkey_b, key = self._handshake
        salt = srp6a.bytes_to_long(self.salt)
        expected_proof = srp6a.calculate_M(
            HASH, N, G, USERNAME, salt, pubkey_a, pubkey_b, key
        )
        if sc1.client_proof != expected_proof:
            raise DeviceFailureError
        self._cipher = AESGCM(key[:32])
        self._iv = bytearray(os.urandom(8) + (1).to_bytes(4, "big"))

        response = session_pb2.SessionData(sec_ver=session_pb2.SecScheme2)
        response.sec2.msg = sec2_pb2.S2Session_Response1
        response.sec2.sr1.status = constants_pb2.Success
        response.sec2.sr1.device_proof = srp6a.calculate_H_AMK(
            HASH, pubkey_a, expected_proof, key
        )
        response.sec2.sr1.device_nonce = bytes(self._iv)
        return response.SerializeToString()

    def _next_iv(self) -> bytes:
        iv = bytes(self._iv)
        counter = int.from_bytes(self._iv[8:], "big") + 1
        self._iv[8:] = counter.to_bytes(4, "big")
        return iv


def _close_without_response(request: web.Request) -> web.StreamResponse:
    if request.transport is not None:
        request.transport.close()
    return web.Response()
