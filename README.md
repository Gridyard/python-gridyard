# python-gridyard

Async Python client for the local API of gridyard devices. Home Assistant uses it in the
[gridyard integration](https://github.com/Gridyard/home-assistant).

**Status:** in development, not ready for use.

## Usage

```python
import aiohttp

from gridyard import GridyardClient

async with aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar()) as websession:
    client = GridyardClient(websession, "192.0.2.10", setup_code)
    info = await client.device_info()
    state = await client.state()
    for value in state.record.values:
        print(value.quantity, value.tariff, value.phase, value.value, value.unit)
```

| Method | Endpoint | Result |
| --- | --- | --- |
| `connect()` | `proto-ver`, `prov-session` | checks reachability, setup code and API version |
| `device_info()` | `device-info` | `DeviceInfo` |
| `state()` | `state` | `State` with the latest `Record`; values are `Decimal`, never `float` |
| `profiles()` | `profiles` | `Catalog`, read page by page |
| `set_profile(id)` | `profile` | returns once the device restarted with the profile |
| `set_meter_key(key)` | `meter-key` | returns once the device answers after its restart |

The client sets up the encrypted session (Security 2) on the first request and again
after a failed one. It sends the session cookie itself, so the `ClientSession` must use
`aiohttp.DummyCookieJar`. Errors derive from `GridyardError`: `CannotConnect`,
`InvalidAuth`, `SessionLost`, and `DeviceError` with one subclass per error code of the
device, such as `ApiUnsupported` or `ProfileUnknown`.

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
