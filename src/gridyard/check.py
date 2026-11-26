"""gridyard-check: open a session with a device and print device-info and state.

The setup code is read from a file so that it does not end up in the shell history.
"""

import argparse
import asyncio
import json
import time
from pathlib import Path

import aiohttp

from .session import DeviceSession

ENDPOINTS = ("device-info", "state")
REQUEST = b'{"api":1}'


async def check(host: str, port: int, setup_code: str) -> None:
    """Set up a session, then read and print each endpoint in ENDPOINTS."""
    async with aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar()) as websession:
        session = DeviceSession(websession, host, setup_code, port=port)
        started = time.monotonic()
        await session.connect()
        print(f"session: {time.monotonic() - started:.2f} s")  # noqa: T201
        for endpoint in ENDPOINTS:
            started = time.monotonic()
            response = await session.request(endpoint, REQUEST)
            print(f"{endpoint}: {time.monotonic() - started:.2f} s")  # noqa: T201
            print(json.dumps(json.loads(response), indent=2))  # noqa: T201


def main() -> None:
    """Run gridyard-check from the command line."""
    parser = argparse.ArgumentParser(
        prog="gridyard-check", description=__doc__.splitlines()[0]
    )
    parser.add_argument(
        "--host", required=True, help="IP address or host name of the device"
    )
    parser.add_argument("--port", type=int, default=80)
    parser.add_argument(
        "--code-file",
        required=True,
        type=Path,
        help="file with the 12-character setup code",
    )
    args = parser.parse_args()
    setup_code = args.code_file.read_text(encoding="utf-8").strip()
    asyncio.run(check(args.host, args.port, setup_code))
