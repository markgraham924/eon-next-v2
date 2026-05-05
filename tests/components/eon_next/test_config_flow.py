"""Tests for the EON Next Fork config flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.eon_next.const import (
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_REFRESH_TOKEN,
    DOMAIN,
)
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import recorder as recorder_helper
from homeassistant.setup import async_setup_component


def _mock_entry(*, email: str = "user@example.com") -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=email,
        data={
            CONF_EMAIL: email,
            CONF_PASSWORD: "secret",
            CONF_REFRESH_TOKEN: "refresh-token",
        },
    )


async def _ensure_recorder(hass) -> None:
    recorder_helper.async_initialize_recorder(hass)
    with patch("homeassistant.components.recorder.ALLOW_IN_MEMORY_DB", True):
        assert await async_setup_component(
            hass,
            "recorder",
            {"recorder": {"db_url": "sqlite://", "commit_interval": 0}},
        )
    await hass.async_block_till_done()
    assert await recorder_helper.async_wait_recorder(hass)


@pytest.mark.asyncio
async def test_reauth_rejects_switching_to_different_account(
    hass,
    enable_custom_integrations: None,
) -> None:
    """Reauth should not allow replacing the entry with another account."""
    del enable_custom_integrations
    await _ensure_recorder(hass)
    entry = _mock_entry()
    entry.add_to_hass(hass)

    with patch(
        "custom_components.eon_next.config_flow.EonNextConfigFlow._validate_credentials",
        AsyncMock(return_value="new-refresh-token"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_REAUTH,
                "entry_id": entry.entry_id,
            },
            data=entry.data,
        )
        assert result["type"] is FlowResultType.FORM

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_EMAIL: "other@example.com",
                CONF_PASSWORD: "secret",
            },
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "unique_id_mismatch"
    assert entry.data[CONF_EMAIL] == "user@example.com"
    assert entry.unique_id == "user@example.com"


@pytest.mark.asyncio
async def test_reauth_updates_credentials_for_same_account(
    hass,
    enable_custom_integrations: None,
) -> None:
    """Reauth should update stored credentials when the account is unchanged."""
    del enable_custom_integrations
    await _ensure_recorder(hass)
    entry = _mock_entry()
    entry.add_to_hass(hass)

    with patch(
        "custom_components.eon_next.config_flow.EonNextConfigFlow._validate_credentials",
        AsyncMock(return_value="updated-refresh-token"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_REAUTH,
                "entry_id": entry.entry_id,
            },
            data=entry.data,
        )
        assert result["type"] is FlowResultType.FORM

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_EMAIL: "USER@example.com",
                CONF_PASSWORD: "new-secret",
            },
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data == {
        CONF_EMAIL: "user@example.com",
        CONF_PASSWORD: "new-secret",
        CONF_REFRESH_TOKEN: "updated-refresh-token",
    }
    assert entry.unique_id == "user@example.com"

