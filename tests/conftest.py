"""Test configuration for local custom component imports."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import pytest_socket

# ── Mock hass_frontend ────────────────────────────────────────────────
# The HA "frontend" component does ``import hass_frontend`` which is a
# compiled JS package only present in full HA installs, not in
# pytest-homeassistant-custom-component environments.  A MagicMock with
# a ``where()`` returning a path prevents ModuleNotFoundError during
# component setup while keeping tests lightweight.
_mock_frontend = MagicMock()
_mock_frontend.where.return_value = Path("/dev/null")
sys.modules.setdefault("hass_frontend", _mock_frontend)

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.hookimpl(trylast=True)
def pytest_runtest_setup() -> None:
    """Re-enable sockets after HA's test plugin setup on Windows/Python 3.12.

    pytest-homeassistant-custom-component 0.13.x disables socket creation during
    setup, but asyncio event loop initialization on this platform uses
    ``socket.socketpair()`` and fails before tests can start.
    """
    pytest_socket.enable_socket()


@pytest.fixture
def event_loop_policy(socket_enabled: None):
    """Ensure sockets are enabled before pytest-asyncio creates an event loop."""
    return asyncio.get_event_loop_policy()
