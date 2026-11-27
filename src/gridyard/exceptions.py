"""Errors raised by gridyard."""

import contextlib
from collections.abc import Iterator


class GridyardError(Exception):
    """Base class for gridyard errors."""


class CannotConnect(GridyardError):
    """The device cannot be reached or did not answer."""


class InvalidAuth(GridyardError):
    """The device rejected the setup code."""


class SessionLost(GridyardError):
    """The session broke during a request and the request was not completed."""


class UpdateFellBack(GridyardError):
    """The device did not confirm the new firmware and runs the previous one again."""

    def __init__(self, message: str, firmware: str) -> None:
        """Store the firmware the device runs after the fallback."""
        super().__init__(message)
        self.firmware = firmware


class DeviceError(GridyardError):
    """The device answered with an error code of the local API contract."""

    code = ""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        retry_after_s: int | None = None,
    ) -> None:
        """Store the retry hints of the error response (JSON endpoints only)."""
        super().__init__(message)
        self.retryable = retryable
        self.retry_after_s = retry_after_s


class ApiUnsupported(DeviceError):
    """The device speaks another version of the local API, or none."""

    code = "API_UNSUPPORTED"


class ConfigInvalid(DeviceError):
    """A field is missing or has the wrong format."""

    code = "CONFIG_INVALID"


class ProfileUnknown(DeviceError):
    """The profile is not in the catalog of the device."""

    code = "PROFILE_UNKNOWN"


class StorageFailed(DeviceError):
    """The device could not store the value."""

    code = "STORAGE_FAILED"


class AuthFailed(DeviceError):
    """The update token is missing, wrong, expired or used."""

    code = "AUTH_FAILED"


class UpdateRejected(DeviceError):
    """The device does not accept the update."""

    code = "UPDATE_REJECTED"


class UpdateFailed(DeviceError):
    """The update failed or another update runs."""

    code = "UPDATE_FAILED"


DEVICE_ERRORS: dict[str, type[DeviceError]] = {
    error.code: error
    for error in (
        ApiUnsupported,
        ConfigInvalid,
        ProfileUnknown,
        StorageFailed,
        AuthFailed,
        UpdateRejected,
        UpdateFailed,
    )
}


def device_error(
    endpoint: str,
    code: str,
    *,
    retryable: bool = False,
    retry_after_s: int | None = None,
) -> DeviceError:
    """Return the exception for an error code; unknown codes give DeviceError."""
    error = DEVICE_ERRORS.get(code, DeviceError)
    return error(
        f"{endpoint}: {code}", retryable=retryable, retry_after_s=retry_after_s
    )


@contextlib.contextmanager
def invalid_response(endpoint: str) -> Iterator[None]:
    """Turn a malformed response (missing field, wrong type) into GridyardError."""
    try:
        yield
    except (KeyError, TypeError, ValueError, AttributeError, ArithmeticError) as err:
        msg = f"{endpoint}: unexpected response: {err!r}"
        raise GridyardError(msg) from err
