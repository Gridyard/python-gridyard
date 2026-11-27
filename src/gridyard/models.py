"""Data of the local API, version `api` 1.

Parsers take the decoded JSON of a response and ignore fields they do not know.
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any

# Unit per quantity `q`, as in the contract (step 03 and the Home Assistant table).
UNITS: dict[str, str | None] = {
    "energy_import": "Wh",
    "energy_export": "Wh",
    "power": "W",
    "power_import": "W",
    "power_export": "W",
    "reactive_energy_import": "varh",
    "reactive_energy_export": "varh",
    "reactive_power": "var",
    "apparent_power": "VA",
    "voltage": "V",
    "current": "A",
    "frequency": "Hz",
    "power_factor": None,
    "phase_angle": "°",
    "heat_energy": "Wh",
    "volume": "m³",
    "flow": "m³/h",
    "flow_temperature": "°C",
    "return_temperature": "°C",
    "operating_time": "s",
}


class Quality(StrEnum):
    """Quality of a record; UNKNOWN stands for a value this library does not know."""

    VALID = "valid"
    PARTIAL = "partial"
    STALE = "stale"
    UNAVAILABLE = "unavailable"
    INVALID = "invalid"
    UNKNOWN = "unknown"

    @classmethod
    def _missing_(cls, _value: object) -> Quality:
        return cls.UNKNOWN


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    """Response of device-info."""

    device_id: str
    firmware: str
    mode: str
    catalog: str
    profile_count: int
    profile_id: str | None
    model: str | None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> DeviceInfo:
        """Parse a device-info response; an empty profile or model becomes None."""
        return cls(
            device_id=data["device_id"],
            firmware=data["fw"],
            mode=data["mode"],
            catalog=data["catalog"],
            profile_count=data["profile_count"],
            profile_id=data["profile"] or None,
            model=data.get("model") or None,
        )


@dataclass(frozen=True, slots=True)
class Profile:
    """Entry of the profile catalog."""

    id: str
    model: str
    rev: int
    encrypted: bool

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Profile:
        """Parse one entry of a profiles page."""
        return cls(
            id=data["id"],
            model=data["model"],
            rev=data["rev"],
            encrypted=data.get("encrypted", False),
        )


@dataclass(frozen=True, slots=True)
class Catalog:
    """Profile catalog of the device, identified by its hash."""

    catalog: str
    profiles: tuple[Profile, ...]


@dataclass(frozen=True, slots=True)
class Value:
    """One measured value.

    tariff 0 is the total; phase 0 is the total, 1 to 3 are L1 to L3, 4 is N.
    """

    quantity: str
    tariff: int
    phase: int
    value: Decimal

    @property
    def unit(self) -> str | None:
        """Unit of the quantity."""
        return UNITS[self.quantity]

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Value:
        """Parse a value; m * 10^e is computed exactly, never as float."""
        return cls(
            quantity=data["q"],
            tariff=data.get("t", 0),
            phase=data.get("p", 0),
            value=Decimal(data["m"]).scaleb(data["e"]),
        )


@dataclass(frozen=True, slots=True)
class Record:
    """Latest record of the meter."""

    quality: Quality
    age_ms: int
    profile_id: str
    values: tuple[Value, ...]

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Record:
        """Parse a record; values with an unknown quantity are left out."""
        values = tuple(
            Value.from_json(value) for value in data["values"] if value["q"] in UNITS
        )
        return cls(
            quality=Quality(data["quality"]),
            age_ms=data["age_ms"],
            profile_id=data["profile"]["id"],
            values=values,
        )


@dataclass(frozen=True, slots=True)
class Axis:
    """State of one axis, such as wifi, meter or update."""

    state: str
    since_ms: int
    error: str | None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Axis:
        """Parse one axis."""
        return cls(state=data["state"], since_ms=data["since_ms"], error=data["error"])


@dataclass(frozen=True, slots=True)
class State:
    """Response of state: the latest record and the device state."""

    device_id: str
    boot_id: str
    uptime_ms: int
    firmware: str
    mode: str
    catalog: str
    record: Record | None
    axes: dict[str, Axis]
    diag: dict[str, Any]

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> State:
        """Parse a state response; without a record, record is None."""
        records = data["record"]["records"]
        state = data["state"]
        return cls(
            device_id=state["device_id"],
            boot_id=state["boot_id"],
            uptime_ms=state["uptime_ms"],
            firmware=state["firmware"],
            mode=state["mode"],
            catalog=state["catalog"],
            record=Record.from_json(records[0]) if records else None,
            axes={name: Axis.from_json(axis) for name, axis in state["axes"].items()},
            diag=state["diag"],
        )
