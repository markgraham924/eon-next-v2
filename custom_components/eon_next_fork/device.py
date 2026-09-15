"""Shared device metadata and entity labels for EON Next Fork."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo

from .const import DOMAIN, INTEGRATION_VERSION
from .eonnext import METER_TYPE_ELECTRIC, METER_TYPE_GAS
from .models import EonNextConfigEntry


def get_entry_device_info(config_entry: EonNextConfigEntry) -> DeviceInfo:
    """Build the top-level Home Assistant service device for a config entry."""
    return DeviceInfo(
        identifiers={(DOMAIN, config_entry.entry_id)},
        entry_type=DeviceEntryType.SERVICE,
        manufacturer="E.ON Next",
        model="Energy Service",
        name=config_entry.title or "E.ON Next",
        sw_version=INTEGRATION_VERSION,
        configuration_url="https://www.eonnext.com/",
    )


def get_account_device_info(
    config_entry: EonNextConfigEntry,
    account_number: str,
) -> DeviceInfo:
    """Build a dedicated Home Assistant device for one account."""
    return DeviceInfo(
        identifiers={(DOMAIN, f"{config_entry.entry_id}::account::{account_number}")},
        manufacturer="E.ON Next",
        model="Energy Account",
        name=account_label(account_number),
        sw_version=INTEGRATION_VERSION,
        configuration_url="https://www.eonnext.com/",
        via_device=(DOMAIN, config_entry.entry_id),
    )


def get_meter_device_info(
    config_entry: EonNextConfigEntry,
    meter,
) -> DeviceInfo:
    """Build a dedicated Home Assistant device for one meter."""
    supply_point_id = str(getattr(meter, "supply_point_id", "") or "")
    serial = str(getattr(meter, "serial", "") or "")
    identifiers = {
        (
            DOMAIN,
            f"{config_entry.entry_id}::meter::{supply_point_id or serial}::{serial}",
        )
    }
    return DeviceInfo(
        identifiers=identifiers,
        manufacturer="E.ON Next",
        model="Export Meter"
        if getattr(meter, "is_export", False)
        else ("Electricity Meter" if getattr(meter, "type", None) == METER_TYPE_ELECTRIC else "Gas Meter"),
        name=meter_label(meter),
        serial_number=serial or None,
        sw_version=INTEGRATION_VERSION,
        configuration_url="https://www.eonnext.com/",
        via_device=(DOMAIN, config_entry.entry_id),
    )


def get_charger_device_info(
    config_entry: EonNextConfigEntry,
    charger,
) -> DeviceInfo:
    """Build a dedicated Home Assistant device for one EV charger/device."""
    device_id = str(getattr(charger, "device_id", "") or "")
    return DeviceInfo(
        identifiers={(DOMAIN, f"{config_entry.entry_id}::charger::{device_id}")},
        manufacturer="E.ON Next",
        model="Smart Charging Device",
        name=charger_label(charger),
        serial_number=device_id or None,
        sw_version=INTEGRATION_VERSION,
        configuration_url="https://www.eonnext.com/",
        via_device=(DOMAIN, config_entry.entry_id),
    )


def get_diagnostics_device_info(config_entry: EonNextConfigEntry) -> DeviceInfo:
    """Build a dedicated device for integration-level diagnostics."""
    return DeviceInfo(
        identifiers={(DOMAIN, f"{config_entry.entry_id}::diagnostics")},
        manufacturer="E.ON Next",
        model="Integration Diagnostics",
        name="Diagnostics",
        sw_version=INTEGRATION_VERSION,
        configuration_url="https://www.eonnext.com/",
        via_device=(DOMAIN, config_entry.entry_id),
    )


def meter_label(meter) -> str:
    """Return a concise, human-friendly meter label."""
    if getattr(meter, "type", None) == METER_TYPE_ELECTRIC:
        base = "Export Meter" if getattr(meter, "is_export", False) else "Electricity Meter"
    elif getattr(meter, "type", None) == METER_TYPE_GAS:
        base = "Gas Meter"
    else:
        base = "Meter"

    suffix = _identity_suffix(getattr(meter, "supply_point_id", ""), getattr(meter, "serial", ""))
    return f"{base} {suffix}" if suffix else base


def charger_label(charger) -> str:
    """Return a concise, human-friendly EV charger label."""
    name = str(getattr(charger, "serial", "") or "").strip()
    suffix = _identity_suffix(getattr(charger, "device_id", ""))
    if name and suffix and not name.endswith(suffix):
        return f"{name} {suffix}"
    if name:
        return name
    return f"EV Charger {suffix}" if suffix else "EV Charger"


def account_label(account_number: str) -> str:
    """Return a concise, human-friendly account label."""
    suffix = _identity_suffix(account_number)
    return f"Account {suffix}" if suffix else "Account"


def _identity_suffix(*values: str) -> str:
    """Pick a short stable suffix from one or more identifiers."""
    for value in values:
        cleaned = str(value or "").strip()
        if cleaned:
            return cleaned[-4:]
    return ""
