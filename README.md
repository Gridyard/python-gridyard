# python-gridyard

Async Python client for the local API of gridyard devices. Home Assistant uses it in the
[gridyard integration](https://github.com/Gridyard/home-assistant).

**Status:** in development, not ready for use.

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

## License

[Apache-2.0](LICENSE)
