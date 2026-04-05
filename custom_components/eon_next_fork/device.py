"""Shared device metadata and entity labels for EON Next Fork."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo

from .const import DOMAIN, INTEGRATION_VERSION
from .eonnext import METER_TYPE_ELECTRIC, METER_TYPE_GAS
from .models import EonNextConfigEntry


def get_entry_device_info(config_entry: EonNextConfigEntry) -> DeviceInfo:
    """Build the shared Home Assistant device for a config entry."""
    return DeviceInfo(
        identifiers={(DOMAIN, config_entry.entry_id)},
        entry_type=DeviceEntryType.SERVICE,
        manufacturer="E.ON Next",
        model="Energy Account",
        name=config_entry.title or "E.ON Next",
        sw_version=INTEGRATION_VERSION,
        configuration_url="https://www.eonnext.com/",
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
