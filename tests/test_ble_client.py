"""Unit tests for the CFX-specific Bleak connection settings."""

import asyncio
import importlib.util
import sys
import unittest
from pathlib import Path
from types import ModuleType


class FakeBleakClientWithServiceCache:
    """Stand-in recording the kwargs the CFX client passes down."""

    def __init__(self, *args, **kwargs) -> None:
        self.connect_kwargs: dict = {}
        self.connected = False

    async def connect(self, **kwargs) -> None:
        self.connect_kwargs = kwargs
        self.connected = True


def _load_ble_client() -> ModuleType:
    """Import ble_client.py with stubbed bleak dependencies."""

    fake_retry = ModuleType("bleak_retry_connector")
    fake_retry.BleakClientWithServiceCache = FakeBleakClientWithServiceCache
    sys.modules["bleak_retry_connector"] = fake_retry

    path = (
        Path(__file__).resolve().parent.parent
        / "custom_components"
        / "dometic_cfx5"
        / "ble_client.py"
    )
    spec = importlib.util.spec_from_file_location("cfx_ble_client", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class TestDometicCFXBleakClient(unittest.TestCase):
    """Verify timeout and cache policies of the CFX client."""

    def setUp(self) -> None:
        self.module = _load_ble_client()

    def _connect(self, client) -> None:
        asyncio.run(client.connect(timeout=20.0))

    def test_connect_uses_cfx_timeout(self) -> None:
        """The 20s default must be replaced with the CFX's 45s timeout."""

        client = self.module.DometicCFXBleakClient()
        self._connect(client)
        self.assertTrue(client.connected)
        self.assertEqual(
            client.connect_kwargs["timeout"],
            self.module.CFX_CONNECT_TIMEOUT_SECONDS,
        )
        self.assertNotIn("dangerous_use_bleak_cache", client.connect_kwargs)

    def test_bonded_reconnect_uses_bluez_cache(self) -> None:
        """Bonded reconnects must allow BlueZ persisted GATT objects."""

        client = self.module.DometicCFXBleakClient(cfx_use_bluez_cache=True)
        self._connect(client)
        self.assertTrue(client.connect_kwargs["dangerous_use_bleak_cache"])


if __name__ == "__main__":
    unittest.main()
