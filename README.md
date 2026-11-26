# python-gridyard

Async Python client for the local API of gridyard devices. Home Assistant uses it in the
[gridyard integration](https://github.com/Gridyard/home-assistant).

**Status:** in development, not ready for use.

## Usage

```python
import aiohttp

from gridyard import DeviceSession

async with aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar()) as websession:
    session = DeviceSession(websession, "192.0.2.10", setup_code)
    response = await session.request("device-info", b'{"api":1}')
```

`DeviceSession` sets up the encrypted session (Security 2) on the first request and
again after a failed one. It sends the session cookie itself, so the `ClientSession`
must use `aiohttp.DummyCookieJar`. Errors: `CannotConnect`, `InvalidAuth`,
`SessionLost`, all subclasses of `GridyardError`.

To check a device from the command line, put the setup code in a file and run:

```sh
gridyard-check --host 192.0.2.10 --code-file setup-code.txt
```

## Development

Requires Python 3.14 and pip 25.1 or later.

```sh
python3.14 -m venv .venv
.venv/bin/pip install --group dev -e .
.venv/bin/ruff check
.venv/bin/ruff format --check
.venv/bin/pytest
```

## Releases

`.github/workflows/release.yml` publishes with PyPI Trusted Publishing, without tokens: every push to TestPyPI,
a tag `v*` also to PyPI.

Code copied from Espressif is listed in [NOTICE](NOTICE).

## License

[Apache-2.0](LICENSE)
