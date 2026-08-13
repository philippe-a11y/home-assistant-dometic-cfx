"""Unit tests for the CFX-specific Bleak connection timing."""

import asyncio
import importlib.util
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace


class FakeBleakError(Exception):
    """Test replacement for bleak.exc.BleakError."""


class FakeBleakClientWithServiceCache:
    """Small stand-in that exposes the methods used by the CFX client."""

    def __init__(
        self,
        manager,
        events: list[str],
        *,
        bluez: bool = True,
        delayed_backend: bool = False,
    ) -> None:
        self._address = "EC:C9:FF:BF:B4:8A"
        self._bluez = bluez
        self.manager = manager
        self.events = events
        self.connect_kwargs = {}
        self.release_connect = asyncio.Event()
        self._backend = None if delayed_backend else self._make_backend()

    @property
    def address(self) -> str:
        """Match HaBleakClientWrapper: address lives on its selected backend."""

        return self._backend.address

    def _make_backend(self):
        return SimpleNamespace(
            address=self._address,
            _device_path="/org/bluez/hci0/dev_EC_C9_FF_BF_B4_8A"
            if self._bluez else None,
            _device_info=(
                {"Connected": self.manager.connected, "Paired": self.manager.paired}
                if self._bluez else None
            ),
            is_connected=self.manager.connected,
        )

    async def connect(self, **kwargs) -> None:
        self.connect_kwargs = kwargs
        self.events.append("connect")
        if self._backend is None:
            self._backend = self._make_backend()
        self.manager.connected = True
        self._backend.is_connected = True
        if self._backend._device_info is not None:
            self._backend._device_info["Connected"] = True
        await self.release_connect.wait()
        self.events.append("services")

    async def pair(self) -> None:
        self.events.append("pair")
        self.manager.paired = True
        if self._backend._device_info is not None:
            self._backend._device_info["Paired"] = True
        self.release_connect.set()


bleak_module = ModuleType("bleak")
bleak_exc_module = ModuleType("bleak.exc")
bleak_exc_module.BleakError = FakeBleakError
connector_module = ModuleType("bleak_retry_connector")
connector_module.BleakClientWithServiceCache = FakeBleakClientWithServiceCache
sys.modules.setdefault("bleak", bleak_module)
sys.modules.setdefault("bleak.exc", bleak_exc_module)
sys.modules.setdefault("bleak_retry_connector", connector_module)

CLIENT_PATH = (
    Path(__file__).parents[1]
    / "custom_components"
    / "dometic_cfx5"
    / "ble_client.py"
)
SPEC = importlib.util.spec_from_file_location("dometic_cfx5_ble_client", CLIENT_PATH)
assert SPEC is not None and SPEC.loader is not None
ble_client = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ble_client
SPEC.loader.exec_module(ble_client)


class FakeBlueZManager:
    """Expose the two BlueZ state queries used by the CFX client."""

    def __init__(self, *, paired: bool = False) -> None:
        self.connected = False
        self.paired = paired

    def is_connected(self, _device_path: str) -> bool:
        return self.connected

    def is_paired(self, _device_path: str) -> bool:
        return self.paired


class BleClientTest(unittest.TestCase):
    """Verify bonding occurs while service discovery is still pending."""

    def setUp(self) -> None:
        ble_client.PAIRING_SETTLE_SECONDS = 0
        ble_client.PHYSICAL_LINK_POLL_SECONDS = 0

    def test_unbonded_bluez_sequence(self) -> None:
        async def run_test() -> None:
            manager = FakeBlueZManager()
            events: list[str] = []
            client = ble_client.DometicCFXBleakClient(manager, events)

            await client.connect()
            self.assertEqual(events, ["connect", "pair", "services"])
            self.assertEqual(
                client.connect_kwargs["timeout"],
                ble_client.CFX_CONNECT_TIMEOUT_SECONDS,
            )

        asyncio.run(run_test())

    def test_existing_bond_uses_normal_connect(self) -> None:
        async def run_test() -> None:
            manager = FakeBlueZManager(paired=True)
            events: list[str] = []
            client = ble_client.DometicCFXBleakClient(
                manager,
                events,
                cfx_use_bluez_cache=True,
            )
            client.release_connect.set()

            await client.connect()
            self.assertEqual(events, ["connect", "services"])
            self.assertTrue(client.connect_kwargs["dangerous_use_bleak_cache"])

        asyncio.run(run_test())

    def test_native_preconnect_pairing_does_not_start_parallel_pair(self) -> None:
        async def run_test() -> None:
            manager = FakeBlueZManager()
            events: list[str] = []
            client = ble_client.DometicCFXBleakClient(
                manager, events, delayed_backend=True
            )
            client._pair_before_connect = True
            client.release_connect.set()

            await client.connect(timeout=20.0)
            self.assertEqual(events, ["connect", "services"])
            self.assertEqual(
                client.connect_kwargs["timeout"],
                ble_client.CFX_CONNECT_TIMEOUT_SECONDS,
            )

        asyncio.run(run_test())

    def test_home_assistant_creates_bluez_backend_inside_connect(self) -> None:
        async def run_test() -> None:
            manager = FakeBlueZManager()
            events: list[str] = []
            client = ble_client.DometicCFXBleakClient(
                manager, events, delayed_backend=True
            )

            await client.connect(timeout=20.0)
            self.assertEqual(events, ["connect", "pair", "services"])
            self.assertEqual(
                client.connect_kwargs["timeout"],
                ble_client.CFX_CONNECT_TIMEOUT_SECONDS,
            )

        asyncio.run(run_test())

    def test_proxy_does_not_force_pairing(self) -> None:
        async def run_test() -> None:
            manager = FakeBlueZManager()
            events: list[str] = []
            client = ble_client.DometicCFXBleakClient(
                manager, events, bluez=False, delayed_backend=True
            )
            client.release_connect.set()
            await client.connect()
            self.assertEqual(events, ["connect", "services"])

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
