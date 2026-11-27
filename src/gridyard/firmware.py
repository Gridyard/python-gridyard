"""Firmware releases: manifest.json and images from the public firmware repository."""

import hashlib
import json

import aiohttp

from .exceptions import CannotConnect, GridyardError, invalid_response
from .models import FirmwareRelease

MANIFEST_URL = (
    "https://github.com/Gridyard/firmware/releases/latest/download/manifest.json"
)
MANIFEST_TIMEOUT_S = 30.0
IMAGE_TIMEOUT_S = 120.0


async def fetch_release(
    websession: aiohttp.ClientSession, url: str = MANIFEST_URL
) -> FirmwareRelease:
    """Read manifest.json of the latest release."""
    body = await _download(websession, url, MANIFEST_TIMEOUT_S)
    with invalid_response("manifest"):
        return FirmwareRelease.from_json(json.loads(body))


async def fetch_image(
    websession: aiohttp.ClientSession, release: FirmwareRelease
) -> bytes:
    """Download the image of a release and check size and SHA-256 before use."""
    image = await _download(websession, release.url, IMAGE_TIMEOUT_S)
    if len(image) != release.size:
        msg = f"image: {len(image)} bytes, manifest says {release.size}"
        raise GridyardError(msg)
    digest = hashlib.sha256(image).hexdigest()
    if digest != release.sha256:
        msg = f"image: SHA-256 {digest}, manifest says {release.sha256}"
        raise GridyardError(msg)
    return image


async def _download(
    websession: aiohttp.ClientSession, url: str, time_limit_s: float
) -> bytes:
    client_timeout = aiohttp.ClientTimeout(total=time_limit_s)
    try:
        async with websession.get(url, timeout=client_timeout) as response:
            response.raise_for_status()
            return await response.read()
    except (aiohttp.ClientError, TimeoutError) as err:
        msg = f"{url}: {type(err).__name__}: {err}"
        raise CannotConnect(msg) from err
