"""Errors raised by gridyard."""


class GridyardError(Exception):
    """Base class for gridyard errors."""


class CannotConnect(GridyardError):
    """The device cannot be reached or did not answer."""


class InvalidAuth(GridyardError):
    """The device rejected the setup code."""


class SessionLost(GridyardError):
    """The session broke during a request and the request was not completed."""
