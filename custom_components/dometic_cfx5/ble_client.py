"""Bleak client with the CFX Android application's bonding sequence."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import Any

from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache

_LOGGER = logging.getLogger(__name__)

# Android deliberately waits two seconds after its connect callback. On BlueZ,
# the CFX physical link can disappear before that delay expires while service
# discovery is pending, so bonding must begin on the first Connected=True edge.
PAIRING_SETTLE_SECONDS = 0.0
PAIRING_TIMEOUT_SECONDS = 15.0
PHYSICAL_LINK_POLL_SECONDS = 0.05
CFX_CONNECT_TIMEOUT_SECONDS = 45.0


class DometicCFXBleakClient(BleakClientWithServiceCache):
    """Connect a CFX and bond after the physical link becomes available.

    Mobile Cooling on Android connects, waits two seconds, creates the bond,
    and only then retrieves services. Bleak's normal ``pair=True`` BlueZ path
    pairs before connecting. A normal post-connect ``pair()`` is too late
    because Bleak waits for the BlueZ Connect call and service discovery first.

    BlueZ can briefly report the device as physically connected while its
    Connect method is still pending. Watching that state lets us reproduce the
    application sequence without replacing Bleak's connection management.
    Non-BlueZ backends, including Home Assistant Bluetooth proxies, retain the
    standard Bleak behavior.
    """

    def __init__(
        self,
        *args: Any,
        cfx_use_bluez_cache: bool = False,
        **kwargs: Any,
    ) -> None:
        """Initialize the client with the bonded-reconnect cache policy."""

        self._cfx_use_bluez_cache = cfx_use_bluez_cache
        super().__init__(*args, **kwargs)

    async def connect(self, **kwargs: Any) -> None:
        """Connect, initiating CFX Just Works bonding at the app's timing."""

        # Do not access ``self.address`` here: HaBleakClientWrapper implements
        # that property through ``self._backend``, which is still None until
        # its connect() method has selected a Bluetooth connection path.
        retry_timeout = kwargs.get("timeout")
        kwargs["timeout"] = CFX_CONNECT_TIMEOUT_SECONDS
        if self._cfx_use_bluez_cache:
            # bleak-retry-connector's service collection cache is in-memory and
            # empty after a HA Core restart. BlueZ itself persists bonded GATT
            # objects, however, and can rebuild Bleak's collection from those
            # objects without waiting forever for ServicesResolved on the CFX5.
            kwargs["dangerous_use_bleak_cache"] = True
            _LOGGER.debug("Using BlueZ persisted GATT objects for bonded CFX reconnect")
        _LOGGER.debug(
            "CFX connection hook active; using %.1fs timeout instead of %s and "
            "waiting for Bluetooth backend",
            CFX_CONNECT_TIMEOUT_SECONDS,
            retry_timeout,
        )

        # With Home Assistant's native pre-connect pairing enabled, Bleak's
        # BlueZ backend calls Device1.Pair instead of issuing a concurrent
        # Device1.Connect. Pair establishes the physical link itself and then
        # proceeds to GATT service resolution. Do not start our Android-timed
        # fallback watcher in parallel with that native BlueZ transaction.
        if getattr(self, "_pair_before_connect", False):
            _LOGGER.debug("Using native BlueZ pre-connect bonding for the CFX")
            await super().connect(**kwargs)
            _LOGGER.debug("CFX BLE link and GATT services are ready")
            return

        # Home Assistant's HaBleakClientWrapper intentionally has no backend
        # yet when this override is entered. It selects and creates the local
        # BlueZ (or proxy) backend inside its own connect() implementation.
        # Therefore the watcher must wait for that backend instead of deciding
        # at method entry that this is not a local BlueZ connection.
        pairing_task = asyncio.create_task(
            self._async_pair_when_backend_is_ready(),
            name="Bond Dometic CFX",
        )
        try:
            await super().connect(**kwargs)
            await pairing_task
            _LOGGER.debug("CFX BLE link and GATT services are ready")
        except BaseException:
            if not pairing_task.done():
                pairing_task.cancel()
                with suppress(asyncio.CancelledError):
                    await pairing_task
            raise

    async def _async_pair_when_backend_is_ready(self) -> None:
        """Wait for Home Assistant to create the backend, then pair on BlueZ."""

        backend = getattr(self, "_backend", None)
        while backend is None:
            await asyncio.sleep(PHYSICAL_LINK_POLL_SECONDS)
            backend = getattr(self, "_backend", None)

        device_path = getattr(backend, "_device_path", None)
        device_info = getattr(backend, "_device_info", None)
        if (
            not isinstance(device_path, str)
            or not device_path.startswith("/org/bluez/")
            or not isinstance(device_info, dict)
        ):
            _LOGGER.debug(
                "CFX %s uses a non-local Bluetooth backend; bonding is delegated",
                self.address,
            )
            return

        _LOGGER.debug(
            "CFX BlueZ backend ready for %s (path=%s, paired=%s, connected=%s)",
            self.address,
            device_path,
            device_info.get("Paired", False),
            device_info.get("Connected", False),
        )
        if device_info.get("Paired", False):
            _LOGGER.debug("CFX %s is already bonded in BlueZ", self.address)
            # The backend can mark itself connected before BlueZ has actually
            # raised Device1.Connected. Using that premature state caused the
            # app-style Pair request to race the still-running Connect method
            # and return org.bluez.Error.InProgress.
            while not device_info.get("Connected", False):
                await asyncio.sleep(PHYSICAL_LINK_POLL_SECONDS)
            _LOGGER.debug("Physical BLE reconnect to bonded CFX %s is up", self.address)
            return

        await self._async_pair_when_physical_link_is_ready(backend, device_info)

    async def _async_pair_when_physical_link_is_ready(
        self, backend: Any, device_info: dict[str, Any]
    ) -> None:
        """Bond after BlueZ reports the short-lived physical CFX link."""

        while not device_info.get("Connected", False):
            await asyncio.sleep(PHYSICAL_LINK_POLL_SECONDS)

        if device_info.get("Paired", False):
            return

        _LOGGER.debug("Physical BLE link to %s is up", self.address)
        if PAIRING_SETTLE_SECONDS:
            await asyncio.sleep(PAIRING_SETTLE_SECONDS)
        if not device_info.get("Connected", False):
            _LOGGER.debug(
                "Physical BLE link to %s dropped before bonding", self.address
            )
            raise BleakError(
                "CFX dropped the physical BLE link before bonding could start"
            )

        _LOGGER.debug("Starting CFX Just Works bonding for %s", self.address)
        async with asyncio.timeout(PAIRING_TIMEOUT_SECONDS):
            await self.pair()
        _LOGGER.debug(
            "CFX bonding call completed for %s (paired=%s, connected=%s)",
            self.address,
            device_info.get("Paired", False),
            device_info.get("Connected", False),
        )
