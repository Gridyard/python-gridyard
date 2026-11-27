"""Test reading releases and images of the firmware repository."""

import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from gridyard import FirmwareRelease, GridyardError, fetch_image, fetch_release

IMAGE = bytes(range(256)) * 16
FILES = web.AppKey("files", dict[str, bytes])


def manifest(server_url: str) -> dict[str, Any]:
    """Return a synthetic manifest.json for IMAGE on the test server."""
    return {
        "version": "v0.6.0",
        "url": f"{server_url}/image.bin",
        "sha256": hashlib.sha256(IMAGE).hexdigest().upper(),
        "size": len(IMAGE),
        "min_api": 1,
        "notes": "Synthetic release",
    }


@pytest.fixture
async def release_server(
    aiohttp_server: Callable[..., Awaitable[TestServer]],
) -> TestServer:
    """Serve manifest.json and image.bin; tests change the served files."""
    app = web.Application()
    files: dict[str, bytes] = {"image.bin": IMAGE}
    app[FILES] = files

    async def serve(request: web.Request) -> web.Response:
        name = request.match_info["name"]
        if name not in files:
            raise web.HTTPNotFound
        return web.Response(body=files[name])

    app.router.add_get("/{name}", serve)
    server = await aiohttp_server(app)
    files["manifest.json"] = json.dumps(manifest(str(server.make_url("")))).encode()
    return server


async def test_fetch_release(release_server: TestServer) -> None:
    """Test manifest.json becomes a release."""
    async with aiohttp.ClientSession() as websession:
        release = await fetch_release(
            websession, str(release_server.make_url("/manifest.json"))
        )

    assert release.version == "v0.6.0"
    assert release.size == len(IMAGE)
    assert release.sha256 == hashlib.sha256(IMAGE).hexdigest()
    assert release.min_api == 1
    assert release.notes == "Synthetic release"


async def test_fetch_release_malformed(release_server: TestServer) -> None:
    """Test a manifest without a required field."""
    release_server.app[FILES]["manifest.json"] = b'{"version": "v0.6.0"}'

    async with aiohttp.ClientSession() as websession:
        with pytest.raises(GridyardError, match="manifest: unexpected response"):
            await fetch_release(
                websession, str(release_server.make_url("/manifest.json"))
            )


async def test_fetch_image(release_server: TestServer) -> None:
    """Test the image is returned when size and SHA-256 match."""
    async with aiohttp.ClientSession() as websession:
        release = await fetch_release(
            websession, str(release_server.make_url("/manifest.json"))
        )
        image = await fetch_image(websession, release)

    assert image == IMAGE


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [("sha256", "0" * 64, "SHA-256"), ("size", 1, "bytes")],
)
async def test_fetch_image_mismatch(
    release_server: TestServer, field: str, value: Any, match: str
) -> None:
    """Test an image that does not match the manifest is refused."""
    data = manifest(str(release_server.make_url("")))
    data[field] = value
    release = FirmwareRelease.from_json(data)

    async with aiohttp.ClientSession() as websession:
        with pytest.raises(GridyardError, match=match):
            await fetch_image(websession, release)
