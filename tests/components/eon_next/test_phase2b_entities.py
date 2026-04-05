"""Unit tests for Phase 2B entities and cost tracker runtime."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.eon_next_fork.cost_tracker import EonNextCostTrackerManager
from custom_components.eon_next_fork.sensor import (
    AccountBalanceSensor,
    PreviousDayConsumptionSensor,
)
from homeassistant.core import Event, State

_REF_UTC = datetime.now(tz=timezone.utc).replace(
    hour=12, minute=0, second=0, microsecond=0
)
_YESTERDAY_MIDNIGHT = (_REF_UTC - timedelta(days=1)).replace(
    hour=0,
    minute=0,
    second=0,
    microsecond=0,
).isoformat()


def _make_coordinator(data):
    coordinator = MagicMock()
    coordinator.data = data
    coordinator.last_update_success = True
    return coordinator


def _make_meter(serial: str = "E10ABC123") -> MagicMock:
    meter = MagicMock()
    meter.serial = serial
    return meter


def test_previous_day_consumption_sensor() -> None:
    """Previous day consumption sensor should expose value and quality attrs."""
    meter = _make_meter()
    coordinator = _make_coordinator(
        {
            meter.serial: {
                "previous_day_consumption": 12.345,
                "previous_day_consumption_entry_count": 46,
                "previous_day_consumption_data_complete": True,
                "previous_day_consumption_last_reset": _YESTERDAY_MIDNIGHT,
            }
        }
    )
    sensor = PreviousDayConsumptionSensor(coordinator, meter)
    assert sensor.native_value == pytest.approx(12.345)
    assert sensor.extra_state_attributes == {"entry_count": 46, "data_complete": True}


def test_account_balance_sensor() -> None:
    """Account balance sensor should expose account attrs."""
    account_number = "A-12345678"
    coordinator = _make_coordinator(
        {
            f"account::{account_number}": {
                "balance": -12.34,
                "last_updated": _REF_UTC.isoformat(),
            }
        }
    )
    sensor = AccountBalanceSensor(coordinator, account_number)
    assert sensor.native_value == pytest.approx(-12.34)
    assert sensor.extra_state_attributes["account_number"] == account_number
    assert sensor.extra_state_attributes["last_updated"] == _REF_UTC.isoformat()


@pytest.mark.asyncio
async def test_cost_tracker_updates_from_energy_state_changes(hass) -> None:
    """Energy-delta updates should accumulate kWh and GBP cost."""
    coordinator = _make_coordinator({"meter-1": {"unit_rate": 0.25}})
    manager = EonNextCostTrackerManager(hass, "entry-1", coordinator)
    manager._store.async_load = AsyncMock(return_value={"trackers": []})  # noqa: SLF001
    manager._store.async_save = AsyncMock()  # noqa: SLF001
    await manager.async_initialize()

    tracker = await manager.async_add_tracker(
        name="Washing Machine",
        tracked_entity_id="sensor.washer_energy",
        meter_serial="meter-1",
    )
    tracker_id = tracker.id

    first = State(
        "sensor.washer_energy",
        "1.0",
        {"unit_of_measurement": "kWh"},
        last_updated=_REF_UTC.replace(hour=9, minute=0),
    )
    second = State(
        "sensor.washer_energy",
        "1.6",
        {"unit_of_measurement": "kWh"},
        last_updated=_REF_UTC.replace(hour=9, minute=30),
    )

    await manager._async_handle_state_change(  # noqa: SLF001
        tracker_id,
        Event("state_changed", {"old_state": None, "new_state": first}),
    )
    await manager._async_handle_state_change(  # noqa: SLF001
        tracker_id,
        Event("state_changed", {"old_state": first, "new_state": second}),
    )

    state = manager.get_state(tracker_id)
    assert state is not None
    assert state.today_consumption_kwh == pytest.approx(0.6)
    assert state.today_cost == pytest.approx(0.15)
