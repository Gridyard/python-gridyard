"""Placeholder test until the client exists."""

from importlib.resources import files

import gridyard


def test_package_is_typed() -> None:
    """Test the package ships the py.typed marker."""
    assert files(gridyard).joinpath("py.typed").is_file()
