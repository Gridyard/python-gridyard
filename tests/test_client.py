"""Test GridyardClient with the contract examples."""

import json
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import aiohttp
import pytest
from aiohttp.test_utils import TestServer

from gridyard import (
    ApiUnsupported,
    AuthFailed,
    DeviceError,
    GridyardClient,
    GridyardError,
    ProfileUnknown,
    Quality,
    UpdateFailed,
    UpdateFellBack,
    UpdateRejected,
    Value,
    client,
)

from .conftest import SETUP_CODE, TIMEOUT_S, load_fixture
from .device import FakeDevice


def answer(response: dict[str, Any]) -> Callable[[bytes], bytes]:
    """Return an endpoint handler that answers with a JSON object."""
    return lambda _data: json.dumps(response).encode()


@pytest.fixture
def gridyard_client(
    server: TestServer, websession: aiohttp.ClientSession
) -> GridyardClient:
    """Return a client for the fake device."""
    return GridyardClient(
        websession, server.host, SETUP_CODE, port=server.port, timeout=TIMEOUT_S
    )


@pytest.fixture
def fast_restart(monkeypatch: pytest.MonkeyPatch) -> None:
    """Poll device-info every 10 ms for at most 0.5 s after a restart."""
    monkeypatch.setattr(client, "RESTART_POLL_INTERVAL_S", 0.01)
    monkeypatch.setattr(client, "RESTART_TIMEOUT_S", 0.5)
    monkeypatch.setattr(client, "UPDATE_CONFIRM_TIMEOUT_S", 0.5)


@pytest.fixture
def device_info(device: FakeDevice) -> dict[str, Any]:
    """Serve device-info from the contract example; changes to the dict show."""
    info = load_fixture("device-info.response")
    device.endpoints["device-info"] = lambda _data: json.dumps(info).encode()
    return info


def serve_catalog(device: FakeDevice, entries: list[dict], page_size: int) -> None:
    """Serve entries as profiles pages; device.catalog_hash can change in between."""
    device.catalog_hash = "caf9acc67c01abf6"

    def profiles(data: bytes) -> bytes:
        offset = json.loads(data)["offset"]
        page = {
            "api": 1,
            "catalog": device.catalog_hash,
            "total": len(entries),
            "offset": offset,
            "profiles": entries[offset : offset + page_size],
        }
        return json.dumps(page).encode()

    device.endpoints["profiles"] = profiles


def catalog_entries(count: int) -> list[dict]:
    """Return the entries of the example page and synthetic ones up to count."""
    entries = load_fixture("profiles.response")["profiles"]
    synthetic = [
        {"id": f"test-profile-{index}", "model": f"Test {index}", "rev": 1}
        for index in range(count - len(entries))
    ]
    return entries + synthetic


def offsets(device: FakeDevice) -> list[int]:
    """Return the offsets of all profiles requests the device got."""
    return [
        json.loads(data)["offset"]
        for name, data in device.applied
        if name == "profiles"
    ]


@pytest.mark.usefixtures("device_info")
async def test_device_info(gridyard_client: GridyardClient) -> None:
    """Test device-info from the contract example."""
    info = await gridyard_client.device_info()

    assert info.device_id == "sm-0123456789ab"
    assert info.firmware == "v0.5.0"
    assert info.mode == "local"
    assert info.catalog == "caf9acc67c01abf6"
    assert info.profile_count == 86
    assert info.profile_id == "sml-ebz-dd3"
    assert info.model == "eBZ DD3"


async def test_device_info_without_profile(
    gridyard_client: GridyardClient, device_info: dict[str, Any]
) -> None:
    """Test an empty profile and model become None."""
    device_info["profile"] = ""
    device_info["model"] = ""

    info = await gridyard_client.device_info()

    assert info.profile_id is None
    assert info.model is None


async def test_device_info_without_model_field(
    gridyard_client: GridyardClient, device_info: dict[str, Any]
) -> None:
    """Test firmware that does not send model yet."""
    del device_info["model"]

    assert (await gridyard_client.device_info()).model is None


async def test_state(gridyard_client: GridyardClient, device: FakeDevice) -> None:
    """Test state from the contract example."""
    device.endpoints["state"] = answer(load_fixture("state.response"))

    state = await gridyard_client.state()

    assert state.device_id == "sm-0123456789ab"
    assert state.boot_id == "0123456789abcdef"
    assert state.firmware == "v0.5.0"
    assert state.mode == "local"
    assert state.axes["meter"].state == "valid"
    assert state.axes["mqtt"].state == "disconnected"
    assert state.axes["wifi"].error is None
    assert state.diag["rssi"] == -61
    assert state.record is not None
    assert state.record.quality is Quality.VALID
    assert state.record.age_ms == 850
    assert state.record.profile_id == "sml-ebz-dd3"
    assert len(state.record.values) == 11
    assert state.record.values[0] == Value("energy_import", 0, 0, Decimal("12345678.9"))
    assert state.record.values[1].tariff == 1
    assert state.record.values[0].unit == "Wh"
    power_l1 = state.record.values[5]
    assert (power_l1.quantity, power_l1.phase, power_l1.value) == (
        "power",
        1,
        Decimal(-150),
    )
    assert power_l1.unit == "W"


async def test_state_ignores_unknown(
    gridyard_client: GridyardClient, device: FakeDevice
) -> None:
    """Test unknown fields, quantities and quality values do not break state."""
    response = load_fixture("state.response")
    record = response["record"]["records"][0]
    record["quality"] = "something_new"
    record["new_field"] = 1
    record["values"].append({"q": "something_new", "m": "1", "e": 0})
    response["state"]["axes"]["new_axis"] = {
        "state": "on",
        "since_ms": 0,
        "error": None,
    }
    response["new_field"] = {"x": 1}
    device.endpoints["state"] = answer(response)

    state = await gridyard_client.state()

    assert state.record is not None
    assert state.record.quality is Quality.UNKNOWN
    assert len(state.record.values) == 11
    assert state.axes["new_axis"].state == "on"


async def test_state_without_record(
    gridyard_client: GridyardClient, device: FakeDevice
) -> None:
    """Test "record": null, as without profile or right after the start."""
    response = load_fixture("state.response")
    response["record"] = None
    device.endpoints["state"] = answer(response)

    state = await gridyard_client.state()

    assert state.record is None
    assert state.boot_id == "0123456789abcdef"


async def test_state_without_state(
    gridyard_client: GridyardClient, device: FakeDevice
) -> None:
    """Test a response without the state part stays an error."""
    response = load_fixture("state.response")
    del response["state"]
    device.endpoints["state"] = answer(response)

    with pytest.raises(GridyardError, match="unexpected response"):
        await gridyard_client.state()


def test_value_is_exact() -> None:
    """Test m * 10^e keeps all digits, which a float would not."""
    value = Value.from_json({"q": "energy_import", "m": "123456789012345678", "e": -3})

    assert value.value == Decimal("123456789012345.678")
    assert str(value.value) == "123456789012345.678"
    assert float(value.value) != value.value


async def test_profiles_reads_all_pages(
    gridyard_client: GridyardClient, device: FakeDevice
) -> None:
    """Test profiles follows the offsets until total."""
    serve_catalog(device, catalog_entries(14), page_size=6)

    catalog = await gridyard_client.profiles()

    assert catalog.catalog == "caf9acc67c01abf6"
    assert len(catalog.profiles) == 14
    assert catalog.profiles[0].id == "dlms-iskra-am550"
    assert catalog.profiles[0].encrypted is True
    assert catalog.profiles[3].encrypted is False
    assert offsets(device) == [0, 6, 12]


async def test_profiles_restart_when_catalog_changes(
    gridyard_client: GridyardClient, device: FakeDevice
) -> None:
    """Test profiles starts again when the catalog hash changes between pages."""
    serve_catalog(device, catalog_entries(14), page_size=6)
    profiles = device.endpoints["profiles"]

    def change_after_first_page(data: bytes) -> bytes:
        response = profiles(data)
        device.catalog_hash = "0000000000000001"
        device.endpoints["profiles"] = profiles
        return response

    device.endpoints["profiles"] = change_after_first_page

    catalog = await gridyard_client.profiles()

    assert catalog.catalog == "0000000000000001"
    assert len(catalog.profiles) == 14
    assert offsets(device) == [0, 6, 0, 6, 12]


async def test_profiles_empty_page(
    gridyard_client: GridyardClient, device: FakeDevice
) -> None:
    """Test an empty page before total ends with an error instead of a loop."""
    serve_catalog(device, catalog_entries(14), page_size=6)
    device.endpoints["profiles"] = answer(
        {
            "api": 1,
            "catalog": "caf9acc67c01abf6",
            "total": 14,
            "offset": 0,
            "profiles": [],
        }
    )

    with pytest.raises(GridyardError, match="empty page"):
        await gridyard_client.profiles()


NEW_BOOT_ID = "fedcba9876543210"


@pytest.fixture
def device_state(device: FakeDevice) -> dict[str, Any]:
    """Serve state from the contract example; changes to the dict show."""
    state = load_fixture("state.response")
    device.endpoints["state"] = lambda _data: json.dumps(state).encode()
    return state


def restart(device: FakeDevice, device_state: dict[str, Any]) -> None:
    """Let the device restart after this answer, with a new boot_id."""
    device.restart_pending = True
    device_state["state"]["boot_id"] = NEW_BOOT_ID


def sent(device: FakeDevice, endpoint: str) -> int:
    """Return how often the device got a request for endpoint."""
    return [name for name, _ in device.applied].count(endpoint)


@pytest.mark.usefixtures("fast_restart")
async def test_set_profile(
    gridyard_client: GridyardClient,
    device: FakeDevice,
    device_info: dict[str, Any],
    device_state: dict[str, Any],
) -> None:
    """Test set_profile returns once the device restarted with the profile."""

    def profile(data: bytes) -> bytes:
        device_info["profile"] = data.decode()
        restart(device, device_state)
        return b"OK"

    device.endpoints["profile"] = profile

    await gridyard_client.set_profile("iec-apator-12ec3")

    assert ("profile", b"iec-apator-12ec3") in device.applied
    assert (await gridyard_client.device_info()).profile_id == "iec-apator-12ec3"


@pytest.mark.usefixtures("fast_restart")
async def test_set_profile_fresh_device(
    gridyard_client: GridyardClient,
    device: FakeDevice,
    device_info: dict[str, Any],
    device_state: dict[str, Any],
) -> None:
    """Test a device without profile and without live record gets one."""
    device_info["profile"] = ""
    device_state["record"] = None

    def profile(data: bytes) -> bytes:
        device_info["profile"] = data.decode()
        restart(device, device_state)
        return b"OK"

    device.endpoints["profile"] = profile

    await gridyard_client.set_profile("iec-apator-12ec3")

    assert sent(device, "profile") == 1


@pytest.mark.usefixtures("fast_restart")
async def test_set_profile_waits_for_restart(
    gridyard_client: GridyardClient,
    device: FakeDevice,
    device_info: dict[str, Any],
    device_state: dict[str, Any],
) -> None:
    """Test set_profile waits for the restart even if device-info is early."""
    state_endpoint = device.endpoints["state"]
    polls_after_profile = 0

    def state(data: bytes) -> bytes:
        nonlocal polls_after_profile
        if device_info["profile"] == "iec-apator-12ec3":
            polls_after_profile += 1
        if polls_after_profile == 3:
            restart(device, device_state)
        return state_endpoint(data)

    def profile(data: bytes) -> bytes:
        device_info["profile"] = data.decode()
        return b"OK"

    device.endpoints["state"] = state
    device.endpoints["profile"] = profile

    await gridyard_client.set_profile("iec-apator-12ec3")

    assert polls_after_profile >= 3


@pytest.mark.usefixtures("fast_restart")
async def test_set_profile_lost_response(
    gridyard_client: GridyardClient,
    device: FakeDevice,
    device_info: dict[str, Any],
    device_state: dict[str, Any],
) -> None:
    """Test a lost answer is not sent again; the restart shows the profile took."""

    def profile(data: bytes) -> bytes:
        device_info["profile"] = data.decode()
        device.lost_responses = 1
        restart(device, device_state)
        return b"OK"

    device.endpoints["profile"] = profile

    await gridyard_client.set_profile("iec-apator-12ec3")

    assert sent(device, "profile") == 1


@pytest.mark.usefixtures("fast_restart", "device_info", "device_state")
async def test_set_profile_no_restart(
    gridyard_client: GridyardClient, device: FakeDevice
) -> None:
    """Test set_profile fails when the device does not restart."""
    device.endpoints["profile"] = lambda _data: b"OK"

    with pytest.raises(GridyardError, match="did not restart"):
        await gridyard_client.set_profile("iec-apator-12ec3")


@pytest.mark.usefixtures("fast_restart", "device_info")
async def test_set_profile_restarted_without_profile(
    gridyard_client: GridyardClient,
    device: FakeDevice,
    device_state: dict[str, Any],
) -> None:
    """Test set_profile fails when the device restarts with another profile."""

    def profile(_data: bytes) -> bytes:
        restart(device, device_state)
        return b"OK"

    device.endpoints["profile"] = profile

    with pytest.raises(GridyardError, match="restarted with sml-ebz-dd3"):
        await gridyard_client.set_profile("iec-apator-12ec3")


@pytest.mark.usefixtures("device_state")
async def test_set_profile_unknown(
    gridyard_client: GridyardClient, device: FakeDevice
) -> None:
    """Test a text endpoint error code becomes its exception."""
    device.endpoints["profile"] = lambda _data: b"PROFILE_UNKNOWN"

    with pytest.raises(ProfileUnknown):
        await gridyard_client.set_profile("no-such-profile")


@pytest.mark.usefixtures("fast_restart", "device_info")
@pytest.mark.parametrize("lost", [False, True], ids=["answered", "answer-lost"])
async def test_set_meter_key(
    gridyard_client: GridyardClient,
    device: FakeDevice,
    device_state: dict[str, Any],
    lost: bool,  # noqa: FBT001
) -> None:
    """Test set_meter_key returns after the restart, also when the answer got lost."""
    key = "00112233445566778899AABBCCDDEEFF"

    def meter_key(_data: bytes) -> bytes:
        device.lost_responses = int(lost)
        restart(device, device_state)
        return b"OK"

    device.endpoints["meter-key"] = meter_key

    await gridyard_client.set_meter_key(key)

    assert ("meter-key", key.encode()) in device.applied
    assert sent(device, "meter-key") == 1


async def test_error_response(
    gridyard_client: GridyardClient, device: FakeDevice
) -> None:
    """Test the JSON error format becomes an exception with its retry hints."""
    device.endpoints["device-info"] = answer(load_fixture("error.response"))

    with pytest.raises(UpdateFailed) as error:
        await gridyard_client.device_info()

    assert error.value.retryable is True
    assert error.value.retry_after_s == 30


async def test_response_with_other_api(
    gridyard_client: GridyardClient, device_info: dict[str, Any]
) -> None:
    """Test a response with another api version."""
    device_info["api"] = 2

    with pytest.raises(ApiUnsupported):
        await gridyard_client.device_info()


@pytest.mark.parametrize(
    "gridyard_entry", [None, {"ver": "2", "cap": []}], ids=["missing", "other"]
)
async def test_proto_ver_without_api(
    gridyard_client: GridyardClient, device: FakeDevice, gridyard_entry: dict | None
) -> None:
    """Test firmware without the local API contract or with another version."""
    versions = load_fixture("proto-ver.local.response")
    versions.pop("gridyard")
    if gridyard_entry is not None:
        versions["gridyard"] = gridyard_entry
    device.proto_ver = json.dumps(versions).encode()

    with pytest.raises(ApiUnsupported):
        await gridyard_client.connect()


async def test_unexpected_response(
    gridyard_client: GridyardClient, device: FakeDevice
) -> None:
    """Test a response without a required field."""
    device.endpoints["device-info"] = answer({"api": 1})

    with pytest.raises(GridyardError, match="unexpected response"):
        await gridyard_client.device_info()


@pytest.mark.usefixtures("device_info")
async def test_requests_carry_api(
    gridyard_client: GridyardClient, device: FakeDevice
) -> None:
    """Test JSON requests carry api 1."""
    await gridyard_client.device_info()

    assert device.applied == [("device-info", b'{"api":1}')]


IMAGE = bytes(range(256)) * 200
NEW_FIRMWARE = "v0.6.0"


def set_update_axis(
    device_state: dict[str, Any], state: str, error: str | None
) -> None:
    """Set the update axis of the served state."""
    device_state["state"]["axes"]["update"] = {
        "state": state,
        "since_ms": 0,
        "error": error,
    }


@pytest.fixture
def update_device(device: FakeDevice) -> FakeDevice:
    """Return the fake device with update-begin and POST /update."""
    device.endpoints["update-begin"] = device.update_begin
    return device


@pytest.mark.usefixtures("fast_restart")
async def test_update(
    gridyard_client: GridyardClient,
    update_device: FakeDevice,
    device_state: dict[str, Any],
) -> None:
    """Test offer, upload in blocks and confirmation after the trial boot."""
    state_endpoint = update_device.endpoints["state"]
    trial_polls = 0

    def installed(version: str) -> None:
        restart(update_device, device_state)
        device_state["state"]["firmware"] = version
        set_update_axis(device_state, "trial", None)

    def state(data: bytes) -> bytes:
        nonlocal trial_polls
        if device_state["state"]["axes"]["update"]["state"] == "trial":
            trial_polls += 1
        if trial_polls == 2:
            set_update_axis(device_state, "idle", None)
        return state_endpoint(data)

    update_device.on_update = installed
    update_device.endpoints["state"] = state
    progress: list[tuple[int, int]] = []

    await gridyard_client.update(
        NEW_FIRMWARE, IMAGE, lambda sent, total: progress.append((sent, total))
    )

    assert update_device.received_image == IMAGE
    assert update_device.update_headers["Content-Length"] == str(len(IMAGE))
    assert "Transfer-Encoding" not in update_device.update_headers
    assert progress[-1] == (len(IMAGE), len(IMAGE))
    assert [sent for sent, _ in progress] == sorted(sent for sent, _ in progress)
    assert trial_polls >= 2


@pytest.mark.usefixtures("fast_restart")
async def test_update_falls_back(
    gridyard_client: GridyardClient,
    update_device: FakeDevice,
    device_state: dict[str, Any],
) -> None:
    """Test a failed trial boot raises UpdateFailed."""

    def fell_back(_version: str) -> None:
        restart(update_device, device_state)
        set_update_axis(device_state, "failed", "UPDATE_FAILED")

    update_device.on_update = fell_back

    with pytest.raises(UpdateFellBack, match=r"fell back to v0\.5\.0") as raised:
        await gridyard_client.update(NEW_FIRMWARE, IMAGE)

    assert raised.value.firmware == "v0.5.0"
    assert not isinstance(raised.value, DeviceError)


@pytest.mark.usefixtures("device_state")
async def test_update_busy(
    gridyard_client: GridyardClient, update_device: FakeDevice
) -> None:
    """Test the device refusing an offer while an update or trial boot runs."""
    update_device.endpoints["update-begin"] = answer(
        {"api": 1, "error": "UPDATE_FAILED", "retryable": True, "retry_after_s": 30}
    )

    with pytest.raises(UpdateFailed) as raised:
        await gridyard_client.update(NEW_FIRMWARE, IMAGE)

    assert raised.value.retry_after_s == 30
    assert update_device.received_image == b""


@pytest.mark.usefixtures("fast_restart", "device_state")
async def test_update_not_confirmed(
    gridyard_client: GridyardClient, update_device: FakeDevice
) -> None:
    """Test an update that the device never confirms."""
    with pytest.raises(GridyardError, match="not confirmed"):
        await gridyard_client.update(NEW_FIRMWARE, IMAGE)

    assert update_device.received_image == IMAGE


@pytest.mark.usefixtures("device_state")
async def test_update_offer_rejected(
    gridyard_client: GridyardClient, update_device: FakeDevice
) -> None:
    """Test a rejected offer sends no image."""
    update_device.endpoints["update-begin"] = answer(
        {
            "api": 1,
            "error": "UPDATE_REJECTED",
            "retryable": False,
            "retry_after_s": None,
        }
    )

    with pytest.raises(UpdateRejected):
        await gridyard_client.update(NEW_FIRMWARE, IMAGE)

    assert update_device.received_image == b""


@pytest.mark.usefixtures("device_state")
async def test_update_wrong_token(
    gridyard_client: GridyardClient, update_device: FakeDevice
) -> None:
    """Test the upload with a token the device did not hand out."""
    update_device.endpoints["update-begin"] = answer(
        {"api": 1, "token": "0" * 32, "expires_s": 60}
    )

    with pytest.raises(AuthFailed):
        await gridyard_client.update(NEW_FIRMWARE, IMAGE)

    assert update_device.received_image == b""
