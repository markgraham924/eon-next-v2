#!/usr/bin/env python3
"""Sensor platform for the EON Next Fork integration."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfEnergy, UnitOfVolume
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .coordinator import ev_data_key
from .cost_tracker import EonNextCostTrackerManager
from .device import (
    account_label,
    charger_label,
    get_account_device_info,
    get_charger_device_info,
    get_diagnostics_device_info,
    get_meter_device_info,
    meter_label,
)
from .eonnext import METER_TYPE_ELECTRIC, METER_TYPE_GAS, ElectricityMeter
from .models import EonNextConfigEntry
from .tariff_helpers import (
    RateInfo,
    build_day_rates,
    get_next_rate,
    get_off_peak_metadata,
    get_previous_rate,
)


def _parse_timestamp(value: Any) -> datetime | None:
    """Parse an ISO8601 datetime string to datetime."""
    if not isinstance(value, str):
        return None
    return dt_util.parse_datetime(value)


def _day_rates(data: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Return today's derived rate windows for a meter."""
    if not data:
        return []
    return build_day_rates(data)


def _upcoming_off_peak_windows(
    data: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Return today's off-peak windows that haven't fully ended yet."""
    now = dt_util.now()
    windows: list[dict[str, Any]] = []
    for rate in _day_rates(data):
        if not rate.get("is_off_peak"):
            continue
        end = _parse_timestamp(rate.get("end"))
        if end is None or end <= now:
            continue
        windows.append(rate)
    return windows


def _current_off_peak_window(
    data: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Return the current off-peak window, if one is active."""
    now = dt_util.now()
    for rate in _upcoming_off_peak_windows(data):
        start = _parse_timestamp(rate.get("start"))
        end = _parse_timestamp(rate.get("end"))
        if start is None or end is None:
            continue
        if start <= now < end:
            return rate
    return None


async def _month_to_date_consumption_from_statistics(
    hass: HomeAssistant,
    meter_serial: str,
    meter_type: str,
) -> float | None:
    """Return month-to-date consumption total from recorder statistics."""
    from .statistics import statistic_id_for_meter

    stat_id = statistic_id_for_meter(meter_serial, meter_type)
    if stat_id is None:
        return None

    local_now = dt_util.now()
    start_of_month = local_now.replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    end_of_today = local_now.replace(
        hour=23, minute=59, second=59, microsecond=999999
    )

    try:
        from homeassistant.components.recorder.statistics import (
            statistics_during_period,
        )
        from homeassistant.helpers.recorder import get_instance

        result = await get_instance(hass).async_add_executor_job(
            statistics_during_period,
            hass,
            start_of_month,
            end_of_today,
            {stat_id},
            "day",
            None,
            {"change"},
        )
    except Exception:  # pylint: disable=broad-except
        return None

    total = 0.0
    found = False
    for stat in result.get(stat_id, []):
        change = stat.get("change")
        if change is None:
            continue
        try:
            value = float(change)
        except (TypeError, ValueError):
            continue
        if value < 0:
            continue
        total += value
        found = True

    return round(total, 3) if found else None


async def async_setup_entry(
    _hass: HomeAssistant,
    config_entry: EonNextConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensors from a config entry."""

    coordinator = config_entry.runtime_data.coordinator
    api = config_entry.runtime_data.api
    backfill = config_entry.runtime_data.backfill
    cost_trackers = config_entry.runtime_data.cost_trackers
    entities: list[SensorEntity] = []
    for account in api.accounts:
        account_number = getattr(account, "account_number", None)
        if account_number:
            entities.append(
                AccountBalanceSensor(
                    coordinator,
                    account_number,
                    get_account_device_info(config_entry, account_number),
                )
            )

        for meter in account.meters:
            meter_device_info = get_meter_device_info(config_entry, meter)
            entities.append(
                LatestReadingDateSensor(coordinator, meter, meter_device_info)
            )

            if meter.type == METER_TYPE_ELECTRIC:
                entities.append(
                    LatestElectricKwhSensor(coordinator, meter, meter_device_info)
                )

            if meter.type == METER_TYPE_GAS:
                entities.append(
                    LatestGasCubicMetersSensor(
                        coordinator, meter, meter_device_info
                    )
                )
                entities.append(
                    LatestGasKwhSensor(coordinator, meter, meter_device_info)
                )

            entities.append(
                DailyConsumptionSensor(coordinator, meter, meter_device_info)
            )
            entities.append(StandingChargeSensor(coordinator, meter, meter_device_info))
            entities.append(
                PreviousDayCostSensor(coordinator, meter, meter_device_info)
            )
            entities.append(
                CurrentUnitRateSensor(coordinator, meter, meter_device_info)
            )
            entities.append(CurrentTariffSensor(coordinator, meter, meter_device_info))
            entities.append(
                CurrentRateTypeSensor(coordinator, meter, meter_device_info)
            )
            entities.append(
                NextRateChangeSensor(coordinator, meter, meter_device_info)
            )
            entities.append(
                LowestRateTodaySensor(coordinator, meter, meter_device_info)
            )
            entities.append(
                HighestRateTodaySensor(coordinator, meter, meter_device_info)
            )
            entities.append(
                OffPeakWindowsTodaySensor(coordinator, meter, meter_device_info)
            )
            entities.append(
                NextOffPeakStartSensor(coordinator, meter, meter_device_info)
            )
            entities.append(
                NextOffPeakEndSensor(coordinator, meter, meter_device_info)
            )
            entities.append(
                OffPeakMinutesRemainingSensor(
                    coordinator, meter, meter_device_info
                )
            )
            entities.append(
                PreviousUnitRateSensor(coordinator, meter, meter_device_info)
            )
            entities.append(NextUnitRateSensor(coordinator, meter, meter_device_info))
            entities.append(
                PreviousDayConsumptionSensor(coordinator, meter, meter_device_info)
            )

            if isinstance(meter, ElectricityMeter) and meter.is_export:
                entities.append(
                    ExportUnitRateSensor(coordinator, meter, meter_device_info)
                )
                entities.append(
                    ExportDailyConsumptionSensor(
                        coordinator, meter, meter_device_info
                    )
                )
                entities.append(
                    ExportEarningsTodaySensor(
                        coordinator, meter, meter_device_info
                    )
                )
                entities.append(
                    ExportEarningsYesterdaySensor(
                        coordinator, meter, meter_device_info
                    )
                )
                entities.append(
                    ExportEarningsMonthToDateSensor(
                        coordinator, meter, meter_device_info
                    )
                )

        for charger in account.ev_chargers:
            charger_device_info = get_charger_device_info(config_entry, charger)
            entities.append(
                SmartChargingScheduleSensor(
                    coordinator, charger, charger_device_info
                )
            )
            entities.append(
                SmartChargingSlotCountSensor(
                    coordinator, charger, charger_device_info
                )
            )
            entities.append(
                NextChargeEnergyAddedSensor(
                    coordinator, charger, charger_device_info
                )
            )
            entities.append(
                PlannedEnergyTodaySensor(
                    coordinator, charger, charger_device_info
                )
            )
            entities.append(
                PlannedChargingMinutesTodaySensor(
                    coordinator, charger, charger_device_info
                )
            )
            entities.append(
                NextChargeStartSensor(coordinator, charger, charger_device_info)
            )
            entities.append(
                NextChargeEndSensor(coordinator, charger, charger_device_info)
            )
            entities.append(
                NextChargeStartSlot2Sensor(
                    coordinator, charger, charger_device_info
                )
            )
            entities.append(
                NextChargeEndSlot2Sensor(coordinator, charger, charger_device_info)
            )

    diagnostics_device_info = get_diagnostics_device_info(config_entry)
    entities.append(
        HistoricalBackfillStatusSensor(
            coordinator, backfill, diagnostics_device_info
        )
    )
    entities.append(NetImportCostTodaySensor(coordinator, diagnostics_device_info))

    tracker_entity_ids = cost_trackers.list_tracker_ids()
    for tracker_id in tracker_entity_ids:
        tracker_config = cost_trackers.get_config(tracker_id)
        tracker_device_info = diagnostics_device_info
        if tracker_config is not None:
            for account in api.accounts:
                for meter in account.meters:
                    if meter.serial == tracker_config.meter_serial:
                        tracker_device_info = get_meter_device_info(
                            config_entry, meter
                        )
                        break
                else:
                    continue
                break
        entities.append(
            CostTrackerSensor(cost_trackers, tracker_id, tracker_device_info)
        )

    async_add_entities(entities)

    known_tracker_ids = set(tracker_entity_ids)

    @callback
    def _handle_tracker_added(tracker_id: str) -> None:
        if tracker_id in known_tracker_ids:
            return
        known_tracker_ids.add(tracker_id)
        tracker_config = cost_trackers.get_config(tracker_id)
        tracker_device_info = diagnostics_device_info
        if tracker_config is not None:
            for account in api.accounts:
                for meter in account.meters:
                    if meter.serial == tracker_config.meter_serial:
                        tracker_device_info = get_meter_device_info(
                            config_entry, meter
                        )
                        break
                else:
                    continue
                break
        async_add_entities(
            [CostTrackerSensor(cost_trackers, tracker_id, tracker_device_info)]
        )

    config_entry.async_on_unload(
        cost_trackers.async_add_list_listener(_handle_tracker_added)
    )


class EonNextSensorBase(CoordinatorEntity, SensorEntity):
    """Base class for EON Next Fork sensors."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator,
        data_key: str,
        device_info: DeviceInfo | None = None,
    ):
        super().__init__(coordinator)
        self._data_key = data_key
        if device_info is not None:
            self._attr_device_info = device_info

    @property
    def _meter_data(self) -> dict[str, Any] | None:
        if self.coordinator.data and self._data_key in self.coordinator.data:
            return self.coordinator.data[self._data_key]
        return None

    @property
    def available(self) -> bool:
        return super().available and self._meter_data is not None


class HistoricalBackfillStatusSensor(CoordinatorEntity, SensorEntity):
    """Diagnostic sensor exposing historical backfill status."""

    def __init__(
        self,
        coordinator,
        backfill_manager,
        device_info: DeviceInfo | None = None,
    ):
        super().__init__(coordinator)
        self._backfill = backfill_manager
        self._attr_name = "Historical Backfill Status"
        self._attr_icon = "mdi:database-clock-outline"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_unique_id = "eon_next_fork__historical_backfill_status"
        self._attr_has_entity_name = True
        if device_info is not None:
            self._attr_device_info = device_info

    async def async_added_to_hass(self) -> None:
        """Register status listener when entity is added."""
        await super().async_added_to_hass()

        @callback
        def _handle_status_update() -> None:
            self.async_write_ha_state()

        self.async_on_remove(self._backfill.async_add_listener(_handle_status_update))

    @property
    def native_value(self):
        return self._backfill.get_status()["state"]

    @property
    def extra_state_attributes(self):
        status = self._backfill.get_status()
        attrs: dict[str, Any] = {
            "enabled": status["enabled"],
            "initialized": status["initialized"],
            "rebuild_done": status["rebuild_done"],
            "lookback_days": status["lookback_days"],
            "total_meters": status["total_meters"],
            "completed_meters": status["completed_meters"],
            "pending_meters": status["pending_meters"],
            "next_start_date": status["next_start_date"],
        }
        meters_progress = status.get("meters_progress", {})
        if meters_progress:
            attrs["meters_progress"] = meters_progress
        return attrs


class LatestReadingDateSensor(EonNextSensorBase):
    """Date of latest meter reading."""

    def __init__(self, coordinator, meter, device_info: DeviceInfo | None = None):
        super().__init__(coordinator, meter.serial, device_info)
        self._attr_name = f"{meter_label(meter)} Reading Date"
        self._attr_device_class = SensorDeviceClass.DATE
        self._attr_icon = "mdi:calendar"
        self._attr_unique_id = f"{meter.serial}__reading_date"

    @property
    def native_value(self):
        data = self._meter_data
        return data.get("latest_reading_date") if data else None


class LatestElectricKwhSensor(EonNextSensorBase):
    """Latest electricity meter reading."""

    def __init__(self, coordinator, meter, device_info: DeviceInfo | None = None):
        super().__init__(coordinator, meter.serial, device_info)
        self._attr_name = f"{meter_label(meter)} Reading"
        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_state_class = SensorStateClass.TOTAL
        self._attr_icon = "mdi:meter-electric-outline"
        self._attr_unique_id = f"{meter.serial}__electricity_kwh"

    @property
    def native_value(self):
        data = self._meter_data
        return data.get("latest_reading") if data else None


class LatestGasKwhSensor(EonNextSensorBase):
    """Latest gas meter reading in kWh."""

    def __init__(self, coordinator, meter, device_info: DeviceInfo | None = None):
        super().__init__(coordinator, meter.serial, device_info)
        self._attr_name = f"{meter_label(meter)} Reading kWh"
        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_state_class = SensorStateClass.TOTAL
        self._attr_icon = "mdi:meter-gas-outline"
        self._attr_unique_id = f"{meter.serial}__gas_kwh"

    @property
    def native_value(self):
        data = self._meter_data
        return data.get("latest_reading_kwh") if data else None


class LatestGasCubicMetersSensor(EonNextSensorBase):
    """Latest gas meter reading in cubic meters."""

    def __init__(self, coordinator, meter, device_info: DeviceInfo | None = None):
        super().__init__(coordinator, meter.serial, device_info)
        self._attr_name = f"{meter_label(meter)} Reading"
        self._attr_device_class = SensorDeviceClass.GAS
        self._attr_native_unit_of_measurement = UnitOfVolume.CUBIC_METERS
        self._attr_state_class = SensorStateClass.TOTAL
        self._attr_icon = "mdi:meter-gas-outline"
        self._attr_unique_id = f"{meter.serial}__gas_m3"

    @property
    def native_value(self):
        data = self._meter_data
        return data.get("latest_reading") if data else None


class DailyConsumptionSensor(EonNextSensorBase):
    """Daily energy consumption from smart meter data."""

    def __init__(self, coordinator, meter, device_info: DeviceInfo | None = None):
        super().__init__(coordinator, meter.serial, device_info)
        self._attr_name = f"{meter_label(meter)} Daily Consumption"
        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_state_class = SensorStateClass.TOTAL
        self._attr_icon = "mdi:lightning-bolt"
        self._attr_unique_id = f"{meter.serial}__daily_consumption"

    @property
    def last_reset(self) -> datetime | None:
        data = self._meter_data
        if not data:
            return None
        raw = data.get("daily_consumption_last_reset")
        if raw:
            parsed = dt_util.parse_datetime(str(raw))
            if parsed:
                return dt_util.as_utc(parsed)
        return None

    @property
    def native_value(self):
        data = self._meter_data
        return data.get("daily_consumption") if data else None


class StandingChargeSensor(EonNextSensorBase):
    """Daily standing charge (inc VAT)."""

    def __init__(self, coordinator, meter, device_info: DeviceInfo | None = None):
        super().__init__(coordinator, meter.serial, device_info)
        self._attr_name = f"{meter_label(meter)} Standing Charge"
        self._attr_device_class = SensorDeviceClass.MONETARY
        self._attr_native_unit_of_measurement = "GBP"
        self._attr_state_class = SensorStateClass.TOTAL
        self._attr_icon = "mdi:cash-clock"
        self._attr_unique_id = f"{meter.serial}__standing_charge"

    @property
    def native_value(self):
        data = self._meter_data
        return data.get("standing_charge") if data else None


class PreviousDayCostSensor(EonNextSensorBase):
    """Previous day's total cost inc VAT (consumption + standing charge)."""

    def __init__(self, coordinator, meter, device_info: DeviceInfo | None = None):
        super().__init__(coordinator, meter.serial, device_info)
        self._attr_name = f"{meter_label(meter)} Previous Day Cost"
        self._attr_device_class = SensorDeviceClass.MONETARY
        self._attr_native_unit_of_measurement = "GBP"
        self._attr_state_class = SensorStateClass.TOTAL
        self._attr_icon = "mdi:currency-gbp"
        self._attr_unique_id = f"{meter.serial}__previous_day_cost"

    @property
    def native_value(self):
        data = self._meter_data
        return data.get("previous_day_cost") if data else None

    @property
    def extra_state_attributes(self):
        data = self._meter_data or {}
        period = data.get("cost_period")
        if period:
            return {"cost_period": period}
        return {}


class PreviousDayConsumptionSensor(EonNextSensorBase):
    """Yesterday's total consumption."""

    def __init__(self, coordinator, meter, device_info: DeviceInfo | None = None):
        super().__init__(coordinator, meter.serial, device_info)
        self._attr_name = f"{meter_label(meter)} Previous Day Consumption"
        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_icon = "mdi:history"
        self._attr_unique_id = f"{meter.serial}__previous_day_consumption"

    @property
    def native_value(self):
        data = self._meter_data
        return data.get("previous_day_consumption") if data else None

    @property
    def extra_state_attributes(self):
        data = self._meter_data or {}
        return {
            "entry_count": data.get("previous_day_consumption_entry_count", 0),
            "data_complete": data.get("previous_day_consumption_data_complete", False),
        }


class CurrentUnitRateSensor(EonNextSensorBase):
    """Current energy unit rate (inc VAT) for use with the HA Energy Dashboard."""

    def __init__(self, coordinator, meter, device_info: DeviceInfo | None = None):
        super().__init__(coordinator, meter.serial, device_info)
        self._attr_name = f"{meter_label(meter)} Current Unit Rate"
        self._attr_device_class = SensorDeviceClass.MONETARY
        self._attr_native_unit_of_measurement = f"GBP/{UnitOfEnergy.KILO_WATT_HOUR}"
        self._attr_icon = "mdi:currency-gbp"
        self._attr_unique_id = f"{meter.serial}__current_unit_rate"
        self._attr_suggested_display_precision = 4

    @property
    def native_value(self):
        data = self._meter_data
        return data.get("unit_rate") if data else None


class CurrentTariffSensor(EonNextSensorBase):
    """Current active tariff name for a meter point."""

    def __init__(self, coordinator, meter, device_info: DeviceInfo | None = None):
        super().__init__(coordinator, meter.serial, device_info)
        self._attr_name = f"{meter_label(meter)} Current Tariff"
        self._attr_icon = "mdi:tag-text-outline"
        self._attr_unique_id = f"{meter.serial}__current_tariff"

    @property
    def native_value(self):
        data = self._meter_data
        return data.get("tariff_name") if data else None

    @property
    def extra_state_attributes(self):
        data = self._meter_data or {}
        attrs: dict[str, Any] = {}
        for key in (
            "tariff_code",
            "tariff_type",
            "tariff_unit_rate",
            "tariff_standing_charge",
            "tariff_valid_from",
            "tariff_valid_to",
        ):
            val = data.get(key)
            if val is not None and val != "":
                attrs[key] = val
        return attrs


class CurrentRateTypeSensor(EonNextSensorBase):
    """Current tariff period type for the meter."""

    def __init__(self, coordinator, meter, device_info: DeviceInfo | None = None):
        super().__init__(coordinator, meter.serial, device_info)
        self._attr_name = f"{meter_label(meter)} Current Rate Type"
        self._attr_icon = "mdi:timeline-clock-outline"
        self._attr_unique_id = f"{meter.serial}__current_rate_type"

    @property
    def native_value(self):
        data = self._meter_data
        if not data:
            return None
        if not data.get("tariff_is_tou", False):
            return "standard"
        return get_off_peak_metadata(data).get("current_rate_name")


class NextRateChangeSensor(EonNextSensorBase):
    """Timestamp of the next tariff period transition."""

    def __init__(self, coordinator, meter, device_info: DeviceInfo | None = None):
        super().__init__(coordinator, meter.serial, device_info)
        self._attr_name = f"{meter_label(meter)} Next Rate Change"
        self._attr_device_class = SensorDeviceClass.TIMESTAMP
        self._attr_icon = "mdi:clock-alert-outline"
        self._attr_unique_id = f"{meter.serial}__next_rate_change"

    @property
    def native_value(self):
        data = self._meter_data
        if not data or not data.get("tariff_is_tou", False):
            return None
        return _parse_timestamp(get_off_peak_metadata(data).get("next_transition"))


class LowestRateTodaySensor(EonNextSensorBase):
    """Lowest published rate in today's derived rate schedule."""

    def __init__(self, coordinator, meter, device_info: DeviceInfo | None = None):
        super().__init__(coordinator, meter.serial, device_info)
        self._attr_name = f"{meter_label(meter)} Lowest Rate Today"
        self._attr_device_class = SensorDeviceClass.MONETARY
        self._attr_native_unit_of_measurement = f"GBP/{UnitOfEnergy.KILO_WATT_HOUR}"
        self._attr_icon = "mdi:arrow-down-bold-circle-outline"
        self._attr_unique_id = f"{meter.serial}__lowest_rate_today"
        self._attr_suggested_display_precision = 4

    @property
    def native_value(self):
        rates = _day_rates(self._meter_data)
        if not rates:
            return None
        return min(float(rate["rate"]) for rate in rates)


class HighestRateTodaySensor(EonNextSensorBase):
    """Highest published rate in today's derived rate schedule."""

    def __init__(self, coordinator, meter, device_info: DeviceInfo | None = None):
        super().__init__(coordinator, meter.serial, device_info)
        self._attr_name = f"{meter_label(meter)} Highest Rate Today"
        self._attr_device_class = SensorDeviceClass.MONETARY
        self._attr_native_unit_of_measurement = f"GBP/{UnitOfEnergy.KILO_WATT_HOUR}"
        self._attr_icon = "mdi:arrow-up-bold-circle-outline"
        self._attr_unique_id = f"{meter.serial}__highest_rate_today"
        self._attr_suggested_display_precision = 4

    @property
    def native_value(self):
        rates = _day_rates(self._meter_data)
        if not rates:
            return None
        return max(float(rate["rate"]) for rate in rates)


class OffPeakWindowsTodaySensor(EonNextSensorBase):
    """Count of today's off-peak windows for time-of-use tariffs."""

    def __init__(self, coordinator, meter, device_info: DeviceInfo | None = None):
        super().__init__(coordinator, meter.serial, device_info)
        self._attr_name = f"{meter_label(meter)} Off Peak Windows Today"
        self._attr_icon = "mdi:clock-fast"
        self._attr_unique_id = f"{meter.serial}__off_peak_windows_today"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self):
        data = self._meter_data
        if not data:
            return None
        if not data.get("tariff_is_tou", False):
            return 0
        return sum(1 for rate in _day_rates(data) if rate.get("is_off_peak"))


class NextOffPeakStartSensor(EonNextSensorBase):
    """Start time of the next off-peak window."""

    def __init__(self, coordinator, meter, device_info: DeviceInfo | None = None):
        super().__init__(coordinator, meter.serial, device_info)
        self._attr_name = f"{meter_label(meter)} Next Off Peak Start"
        self._attr_device_class = SensorDeviceClass.TIMESTAMP
        self._attr_icon = "mdi:clock-start"
        self._attr_unique_id = f"{meter.serial}__next_off_peak_start"

    @property
    def native_value(self):
        data = self._meter_data
        if not data or not data.get("tariff_is_tou", False):
            return None
        now = dt_util.now()
        current = _current_off_peak_window(data)
        windows = _upcoming_off_peak_windows(data)
        for window in windows:
            start = _parse_timestamp(window.get("start"))
            end = _parse_timestamp(window.get("end"))
            if start is None or end is None:
                continue
            if current is not None and start <= now < end:
                continue
            if start > now:
                return start
        return None


class NextOffPeakEndSensor(EonNextSensorBase):
    """End time of the active or next off-peak window."""

    def __init__(self, coordinator, meter, device_info: DeviceInfo | None = None):
        super().__init__(coordinator, meter.serial, device_info)
        self._attr_name = f"{meter_label(meter)} Next Off Peak End"
        self._attr_device_class = SensorDeviceClass.TIMESTAMP
        self._attr_icon = "mdi:clock-end"
        self._attr_unique_id = f"{meter.serial}__next_off_peak_end"

    @property
    def native_value(self):
        data = self._meter_data
        if not data or not data.get("tariff_is_tou", False):
            return None
        current = _current_off_peak_window(data)
        if current is not None:
            return _parse_timestamp(current.get("end"))
        windows = _upcoming_off_peak_windows(data)
        if not windows:
            return None
        return _parse_timestamp(windows[0].get("end"))


class OffPeakMinutesRemainingSensor(EonNextSensorBase):
    """Minutes remaining in the current off-peak window."""

    def __init__(self, coordinator, meter, device_info: DeviceInfo | None = None):
        super().__init__(coordinator, meter.serial, device_info)
        self._attr_name = f"{meter_label(meter)} Off Peak Minutes Remaining"
        self._attr_icon = "mdi:timer-sand"
        self._attr_unique_id = f"{meter.serial}__off_peak_minutes_remaining"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self):
        current = _current_off_peak_window(self._meter_data)
        if current is None:
            return 0
        end = _parse_timestamp(current.get("end"))
        if end is None:
            return 0
        delta = end - dt_util.now()
        return max(int(delta.total_seconds() // 60), 0)


class AccountBalanceSensor(EonNextSensorBase):
    """Account balance in pounds."""

    def __init__(
        self,
        coordinator,
        account_number: str,
        device_info: DeviceInfo | None = None,
    ):
        super().__init__(coordinator, f"account::{account_number}", device_info)
        self._account_number = account_number
        self._attr_name = f"{account_label(account_number)} Balance"
        self._attr_device_class = SensorDeviceClass.MONETARY
        self._attr_native_unit_of_measurement = "GBP"
        self._attr_icon = "mdi:wallet-outline"
        self._attr_unique_id = f"{account_number}__account_balance"

    @property
    def native_value(self):
        data = self._meter_data
        return data.get("balance") if data else None

    @property
    def extra_state_attributes(self):
        data = self._meter_data or {}
        return {
            "account_number": self._account_number,
            "last_updated": data.get("last_updated"),
        }


class NetImportCostTodaySensor(CoordinatorEntity, SensorEntity):
    """Today's net import cost across all loaded meters."""

    def __init__(self, coordinator, device_info: DeviceInfo | None = None):
        super().__init__(coordinator)
        self._attr_name = "Net Import Cost Today"
        self._attr_device_class = SensorDeviceClass.MONETARY
        self._attr_native_unit_of_measurement = "GBP"
        self._attr_icon = "mdi:scale-balance"
        self._attr_unique_id = "eon_next_fork__net_import_cost_today"
        self._attr_has_entity_name = True
        if device_info is not None:
            self._attr_device_info = device_info

    @property
    def native_value(self):
        if not self.coordinator.data:
            return None
        import_total = 0.0
        export_total = 0.0
        found = False
        for data in self.coordinator.data.values():
            if data.get("type") not in ("electricity", "gas"):
                continue
            consumption = data.get("daily_consumption")
            unit_rate = data.get("unit_rate")
            standing_charge = data.get("standing_charge")
            if consumption is None or unit_rate is None:
                continue
            try:
                usage_value = float(consumption) * float(unit_rate)
            except (TypeError, ValueError):
                continue
            found = True
            if data.get("is_export", False):
                export_total += usage_value
            else:
                import_total += usage_value + (
                    float(standing_charge) if standing_charge is not None else 0.0
                )
        if not found:
            return None
        return round(import_total - export_total, 4)

    @property
    def extra_state_attributes(self):
        if not self.coordinator.data:
            return {}
        import_total = 0.0
        export_total = 0.0
        for data in self.coordinator.data.values():
            if data.get("type") not in ("electricity", "gas"):
                continue
            consumption = data.get("daily_consumption")
            unit_rate = data.get("unit_rate")
            standing_charge = data.get("standing_charge")
            if consumption is None or unit_rate is None:
                continue
            try:
                usage_value = float(consumption) * float(unit_rate)
            except (TypeError, ValueError):
                continue
            if data.get("is_export", False):
                export_total += usage_value
            else:
                import_total += usage_value + (
                    float(standing_charge) if standing_charge is not None else 0.0
                )
        return {
            "import_cost_today": round(import_total, 4),
            "export_credit_today": round(export_total, 4),
        }


class SmartChargingScheduleSensor(EonNextSensorBase):
    """Smart charging schedule status."""

    def __init__(self, coordinator, charger, device_info: DeviceInfo | None = None):
        super().__init__(coordinator, ev_data_key(charger.device_id), device_info)
        self._attr_name = f"{charger_label(charger)} Schedule"
        self._attr_icon = "mdi:ev-station"
        self._attr_unique_id = f"{charger.device_id}__smart_charging_schedule"

    @property
    def native_value(self):
        data = self._meter_data
        if not data:
            return None

        schedule = data.get("schedule", [])
        if schedule:
            return "Active"
        return "No Schedule"

    @property
    def extra_state_attributes(self):
        data = self._meter_data or {}
        schedule = data.get("schedule", [])
        attrs: dict[str, Any] = {
            "schedule": schedule,
            "slot_count": len(schedule),
        }
        if data.get("next_charge_start") is not None:
            attrs["next_charge_start"] = data.get("next_charge_start")
        if data.get("next_charge_end") is not None:
            attrs["next_charge_end"] = data.get("next_charge_end")
        if data.get("next_charge_start_2") is not None:
            attrs["next_charge_start_2"] = data.get("next_charge_start_2")
        if data.get("next_charge_end_2") is not None:
            attrs["next_charge_end_2"] = data.get("next_charge_end_2")
        if schedule:
            first_slot = schedule[0]
            if first_slot.get("type") is not None:
                attrs["next_slot_type"] = first_slot.get("type")
            if first_slot.get("energy_added_kwh") is not None:
                attrs["next_slot_energy_added_kwh"] = first_slot.get(
                    "energy_added_kwh"
                )
        return attrs


class SmartChargingSlotCountSensor(EonNextSensorBase):
    """Number of currently planned smart-charging slots."""

    def __init__(self, coordinator, charger, device_info: DeviceInfo | None = None):
        super().__init__(coordinator, ev_data_key(charger.device_id), device_info)
        self._attr_name = f"{charger_label(charger)} Slot Count"
        self._attr_icon = "mdi:counter"
        self._attr_unique_id = f"{charger.device_id}__smart_charging_slot_count"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self):
        data = self._meter_data or {}
        return len(data.get("schedule", []))


class NextChargeEnergyAddedSensor(EonNextSensorBase):
    """Planned energy to add in the next smart-charging slot."""

    def __init__(self, coordinator, charger, device_info: DeviceInfo | None = None):
        super().__init__(coordinator, ev_data_key(charger.device_id), device_info)
        self._attr_name = f"{charger_label(charger)} Next Charge Energy Added"
        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_icon = "mdi:battery-arrow-up-outline"
        self._attr_unique_id = f"{charger.device_id}__next_charge_energy_added"

    @property
    def native_value(self):
        data = self._meter_data or {}
        schedule = data.get("schedule", [])
        if not schedule:
            return None
        energy = schedule[0].get("energy_added_kwh")
        if energy is None:
            return None
        try:
            return float(energy)
        except (TypeError, ValueError):
            return None


class PlannedEnergyTodaySensor(EonNextSensorBase):
    """Total planned smart-charging energy for today's local schedule."""

    def __init__(self, coordinator, charger, device_info: DeviceInfo | None = None):
        super().__init__(coordinator, ev_data_key(charger.device_id), device_info)
        self._attr_name = f"{charger_label(charger)} Planned Energy Today"
        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_icon = "mdi:ev-station"
        self._attr_unique_id = f"{charger.device_id}__planned_energy_today"

    @property
    def native_value(self):
        data = self._meter_data or {}
        schedule = data.get("schedule", [])
        today = dt_util.now().date()
        total = 0.0
        found = False
        for slot in schedule:
            start = _parse_timestamp(slot.get("start"))
            if start is None or start.date() != today:
                continue
            energy = slot.get("energy_added_kwh")
            if energy is None:
                continue
            try:
                total += float(energy)
            except (TypeError, ValueError):
                continue
            found = True
        return round(total, 3) if found else None


class PlannedChargingMinutesTodaySensor(EonNextSensorBase):
    """Total planned smart-charging minutes for today's local schedule."""

    def __init__(self, coordinator, charger, device_info: DeviceInfo | None = None):
        super().__init__(coordinator, ev_data_key(charger.device_id), device_info)
        self._attr_name = f"{charger_label(charger)} Planned Minutes Today"
        self._attr_icon = "mdi:timer-outline"
        self._attr_unique_id = f"{charger.device_id}__planned_minutes_today"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self):
        data = self._meter_data or {}
        schedule = data.get("schedule", [])
        today = dt_util.now().date()
        total_minutes = 0
        found = False
        for slot in schedule:
            start = _parse_timestamp(slot.get("start"))
            end = _parse_timestamp(slot.get("end"))
            if start is None or end is None or start.date() != today:
                continue
            total_minutes += max(int((end - start).total_seconds() // 60), 0)
            found = True
        return total_minutes if found else None


class NextChargeStartSensor(EonNextSensorBase):
    """Start time of next EV charge slot."""

    def __init__(self, coordinator, charger, device_info: DeviceInfo | None = None):
        super().__init__(coordinator, ev_data_key(charger.device_id), device_info)
        self._attr_name = f"{charger_label(charger)} Next Charge Start"
        self._attr_device_class = SensorDeviceClass.TIMESTAMP
        self._attr_icon = "mdi:clock-start"
        self._attr_unique_id = f"{charger.device_id}__next_charge_start"

    @property
    def native_value(self):
        data = self._meter_data
        if not data:
            return None
        return _parse_timestamp(data.get("next_charge_start"))


class NextChargeEndSensor(EonNextSensorBase):
    """End time of next EV charge slot."""

    def __init__(self, coordinator, charger, device_info: DeviceInfo | None = None):
        super().__init__(coordinator, ev_data_key(charger.device_id), device_info)
        self._attr_name = f"{charger_label(charger)} Next Charge End"
        self._attr_device_class = SensorDeviceClass.TIMESTAMP
        self._attr_icon = "mdi:clock-end"
        self._attr_unique_id = f"{charger.device_id}__next_charge_end"

    @property
    def native_value(self):
        data = self._meter_data
        if not data:
            return None
        return _parse_timestamp(data.get("next_charge_end"))


class NextChargeStartSlot2Sensor(EonNextSensorBase):
    """Start time of the second EV charge slot."""

    def __init__(self, coordinator, charger, device_info: DeviceInfo | None = None):
        super().__init__(coordinator, ev_data_key(charger.device_id), device_info)
        self._attr_name = f"{charger_label(charger)} Next Charge Start 2"
        self._attr_device_class = SensorDeviceClass.TIMESTAMP
        self._attr_icon = "mdi:clock-start"
        self._attr_unique_id = f"{charger.device_id}__next_charge_start_2"

    @property
    def native_value(self):
        data = self._meter_data
        if not data:
            return None
        return _parse_timestamp(data.get("next_charge_start_2"))


class NextChargeEndSlot2Sensor(EonNextSensorBase):
    """End time of the second EV charge slot."""

    def __init__(self, coordinator, charger, device_info: DeviceInfo | None = None):
        super().__init__(coordinator, ev_data_key(charger.device_id), device_info)
        self._attr_name = f"{charger_label(charger)} Next Charge End 2"
        self._attr_device_class = SensorDeviceClass.TIMESTAMP
        self._attr_icon = "mdi:clock-end"
        self._attr_unique_id = f"{charger.device_id}__next_charge_end_2"

    @property
    def native_value(self):
        data = self._meter_data
        if not data:
            return None
        return _parse_timestamp(data.get("next_charge_end_2"))


class PreviousUnitRateSensor(EonNextSensorBase):
    """Most recent unit rate that differs from the current rate."""

    def __init__(self, coordinator, meter, device_info: DeviceInfo | None = None):
        super().__init__(coordinator, meter.serial, device_info)
        self._attr_name = f"{meter_label(meter)} Previous Unit Rate"
        self._attr_device_class = SensorDeviceClass.MONETARY
        self._attr_native_unit_of_measurement = f"GBP/{UnitOfEnergy.KILO_WATT_HOUR}"
        self._attr_icon = "mdi:currency-gbp"
        self._attr_unique_id = f"{meter.serial}__previous_unit_rate"
        self._attr_suggested_display_precision = 4
        self._rate_info: RateInfo | None = None

    @callback
    def _handle_coordinator_update(self) -> None:
        data = self._meter_data
        self._rate_info = get_previous_rate(data) if data else None
        super()._handle_coordinator_update()

    def _get_rate_info(self) -> RateInfo | None:
        if self._rate_info is not None:
            return self._rate_info
        data = self._meter_data
        return get_previous_rate(data) if data else None

    @property
    def native_value(self):
        info = self._get_rate_info()
        return info.rate if info else None

    @property
    def extra_state_attributes(self):
        data = self._meter_data
        if not data:
            return {}
        info = self._get_rate_info()
        if not info:
            return {}
        attrs: dict[str, Any] = {}
        if info.valid_from is not None:
            attrs["valid_from"] = info.valid_from
        if info.valid_to is not None:
            attrs["valid_to"] = info.valid_to
        tariff_code = data.get("tariff_code")
        if tariff_code:
            attrs["tariff_code"] = tariff_code
        return attrs


class NextUnitRateSensor(EonNextSensorBase):
    """Next upcoming unit rate that differs from the current rate."""

    def __init__(self, coordinator, meter, device_info: DeviceInfo | None = None):
        super().__init__(coordinator, meter.serial, device_info)
        self._attr_name = f"{meter_label(meter)} Next Unit Rate"
        self._attr_device_class = SensorDeviceClass.MONETARY
        self._attr_native_unit_of_measurement = f"GBP/{UnitOfEnergy.KILO_WATT_HOUR}"
        self._attr_icon = "mdi:currency-gbp"
        self._attr_unique_id = f"{meter.serial}__next_unit_rate"
        self._attr_suggested_display_precision = 4
        self._rate_info: RateInfo | None = None

    @callback
    def _handle_coordinator_update(self) -> None:
        data = self._meter_data
        self._rate_info = get_next_rate(data) if data else None
        super()._handle_coordinator_update()

    def _get_rate_info(self) -> RateInfo | None:
        if self._rate_info is not None:
            return self._rate_info
        data = self._meter_data
        return get_next_rate(data) if data else None

    @property
    def native_value(self):
        info = self._get_rate_info()
        return info.rate if info else None

    @property
    def extra_state_attributes(self):
        data = self._meter_data
        if not data:
            return {}
        info = self._get_rate_info()
        if not info:
            return {}
        attrs: dict[str, Any] = {}
        if info.valid_from is not None:
            attrs["valid_from"] = info.valid_from
        if info.valid_to is not None:
            attrs["valid_to"] = info.valid_to
        tariff_code = data.get("tariff_code")
        if tariff_code:
            attrs["tariff_code"] = tariff_code
        return attrs


class ExportUnitRateSensor(EonNextSensorBase):
    """Current export unit rate for export meters."""

    def __init__(self, coordinator, meter, device_info: DeviceInfo | None = None):
        super().__init__(coordinator, meter.serial, device_info)
        self._attr_name = f"{meter_label(meter)} Unit Rate"
        self._attr_device_class = SensorDeviceClass.MONETARY
        self._attr_native_unit_of_measurement = f"GBP/{UnitOfEnergy.KILO_WATT_HOUR}"
        self._attr_icon = "mdi:solar-power"
        self._attr_unique_id = f"{meter.serial}__export_unit_rate"
        self._attr_suggested_display_precision = 4

    @property
    def native_value(self):
        data = self._meter_data
        return data.get("unit_rate") if data else None

    @property
    def extra_state_attributes(self):
        data = self._meter_data or {}
        attrs: dict[str, Any] = {}
        for key in (
            "tariff_code",
            "tariff_name",
            "tariff_valid_from",
            "tariff_valid_to",
        ):
            val = data.get(key)
            if val is not None and val != "":
                attrs[key] = val
        return attrs


class ExportDailyConsumptionSensor(EonNextSensorBase):
    """Daily export consumption for export meters."""

    def __init__(self, coordinator, meter, device_info: DeviceInfo | None = None):
        super().__init__(coordinator, meter.serial, device_info)
        self._attr_name = f"{meter_label(meter)} Daily Consumption"
        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_state_class = SensorStateClass.TOTAL
        self._attr_icon = "mdi:solar-power"
        self._attr_unique_id = f"{meter.serial}__export_daily_consumption"

    @property
    def last_reset(self) -> datetime | None:
        data = self._meter_data
        if not data:
            return None
        raw = data.get("daily_consumption_last_reset")
        if raw:
            parsed = dt_util.parse_datetime(str(raw))
            if parsed:
                return dt_util.as_utc(parsed)
        return None

    @property
    def native_value(self):
        data = self._meter_data
        return data.get("daily_consumption") if data else None


class ExportEarningsTodaySensor(EonNextSensorBase):
    """Derived export earnings for today using current export rate."""

    def __init__(self, coordinator, meter, device_info: DeviceInfo | None = None):
        super().__init__(coordinator, meter.serial, device_info)
        self._attr_name = f"{meter_label(meter)} Earnings Today"
        self._attr_device_class = SensorDeviceClass.MONETARY
        self._attr_native_unit_of_measurement = "GBP"
        self._attr_icon = "mdi:cash-multiple"
        self._attr_unique_id = f"{meter.serial}__export_earnings_today"

    @property
    def native_value(self):
        data = self._meter_data or {}
        consumption = data.get("daily_consumption")
        unit_rate = data.get("unit_rate")
        if consumption is None or unit_rate is None:
            return None
        try:
            return round(float(consumption) * float(unit_rate), 4)
        except (TypeError, ValueError):
            return None


class ExportEarningsYesterdaySensor(EonNextSensorBase):
    """Derived export earnings for yesterday using current export rate."""

    def __init__(self, coordinator, meter, device_info: DeviceInfo | None = None):
        super().__init__(coordinator, meter.serial, device_info)
        self._attr_name = f"{meter_label(meter)} Earnings Yesterday"
        self._attr_device_class = SensorDeviceClass.MONETARY
        self._attr_native_unit_of_measurement = "GBP"
        self._attr_icon = "mdi:cash-clock"
        self._attr_unique_id = f"{meter.serial}__export_earnings_yesterday"

    @property
    def native_value(self):
        data = self._meter_data or {}
        consumption = data.get("previous_day_consumption")
        unit_rate = data.get("unit_rate")
        if consumption is None or unit_rate is None:
            return None
        try:
            return round(float(consumption) * float(unit_rate), 4)
        except (TypeError, ValueError):
            return None


class ExportEarningsMonthToDateSensor(EonNextSensorBase):
    """Derived export earnings month-to-date from recorder statistics."""

    def __init__(self, coordinator, meter, device_info: DeviceInfo | None = None):
        super().__init__(coordinator, meter.serial, device_info)
        self._meter_type = getattr(meter, "type", "")
        self._attr_name = f"{meter_label(meter)} Earnings Month To Date"
        self._attr_device_class = SensorDeviceClass.MONETARY
        self._attr_native_unit_of_measurement = "GBP"
        self._attr_icon = "mdi:cash-check"
        self._attr_unique_id = f"{meter.serial}__export_earnings_month_to_date"
        self._value: float | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.async_add_listener(self._schedule_refresh)
        )
        self._schedule_refresh()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._schedule_refresh()
        super()._handle_coordinator_update()

    @callback
    def _schedule_refresh(self) -> None:
        if self.hass is not None:
            self.hass.async_create_task(self._async_refresh_value())

    async def _async_refresh_value(self) -> None:
        data = self._meter_data or {}
        unit_rate = data.get("unit_rate")
        if unit_rate is None or self.hass is None:
            self._value = None
            self.async_write_ha_state()
            return
        total = await _month_to_date_consumption_from_statistics(
            self.hass,
            self._data_key,
            self._meter_type,
        )
        if total is None:
            self._value = None
        else:
            try:
                self._value = round(float(total) * float(unit_rate), 4)
            except (TypeError, ValueError):
                self._value = None
        self.async_write_ha_state()

    @property
    def native_value(self):
        return self._value

    @property
    def extra_state_attributes(self):
        return {"rate_assumption": "current_unit_rate"}


class CostTrackerSensor(RestoreEntity, SensorEntity):
    """User-defined cost tracker sensor."""

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = "GBP"
    _attr_state_class = SensorStateClass.TOTAL
    _attr_icon = "mdi:cash-plus"
    _attr_suggested_display_precision = 4
    _attr_has_entity_name = True

    def __init__(
        self,
        manager: EonNextCostTrackerManager,
        tracker_id: str,
        device_info: DeviceInfo | None = None,
    ) -> None:
        self._manager = manager
        self._tracker_id = tracker_id
        self._attr_unique_id = f"cost_tracker__{self._manager.entry_id}__{tracker_id}"
        config = self._manager.get_config(tracker_id)
        display_name = config.name if config else tracker_id
        self._attr_name = f"Cost Tracker {display_name}"
        if device_info is not None:
            self._attr_device_info = device_info

    async def async_added_to_hass(self) -> None:
        """Register tracker-state listener."""
        await super().async_added_to_hass()

        @callback
        def _on_tracker_update() -> None:
            self.async_write_ha_state()

        self.async_on_remove(
            self._manager.async_add_state_listener(self._tracker_id, _on_tracker_update)
        )

    @property
    def available(self) -> bool:
        return self._manager.has_tracker(self._tracker_id)

    @property
    def native_value(self):
        state = self._manager.get_state(self._tracker_id)
        if state is None:
            return None
        return state.today_cost

    @property
    def last_reset(self) -> datetime | None:
        state = self._manager.get_state(self._tracker_id)
        if state is None or not state.last_reset:
            return None
        parsed = dt_util.parse_datetime(state.last_reset)
        return dt_util.as_utc(parsed) if parsed else None

    @property
    def extra_state_attributes(self):
        config = self._manager.get_config(self._tracker_id)
        state = self._manager.get_state(self._tracker_id)
        if config is None or state is None:
            return {}
        return {
            "tracked_entity": config.tracked_entity_id,
            "meter_serial": config.meter_serial,
            "today_consumption_kwh": round(state.today_consumption_kwh, 6),
            "today_cost": state.today_cost,
            "last_reset": state.last_reset,
            "enabled": config.enabled,
            "entry_id": self._manager.entry_id,
        }
