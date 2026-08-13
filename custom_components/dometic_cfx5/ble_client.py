"""Bleak client tuned for the CFX's slow GATT bring-up."""

from __future__ import annotations

import logging
from typing import Any

from bleak_retry_connector import BleakClientWithServiceCache

_LOGGER = logging.getLogger(__name__)

CFX_CONNECT_TIMEOUT_SECONDS = 45.0


class DometicCFXBleakClient(BleakClientWithServiceCache):
    """Connect to a CFX that BlueZ has already bonded.

    All local BlueZ bonding runs in ``async_prepare_cfx_bluez`` *before* this
    client connects: fresh Just Works pairing for unbonded coolers and the
    ``Device1.Pair`` preflight for bonded ones. Home Assistant Bluetooth
    proxies bond through Bleak's native ``pair=True`` path instead.

    An earlier revision additionally watched Bleak's BlueZ backend during
    ``connect()`` and issued an app-timed ``pair()`` on the first physical
    link. That watcher polled ``backend._device_info``, which is a snapshot
    from discovery time: immediately after a successful fresh pairing it
    still reported ``Paired=False``/``Connected=False``, so the watcher spun
    until the whole setup was cancelled. With bonding fully handled before
    connect, no in-connect pairing is needed at all.
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
        """Connect with the CFX's extended timeout."""

        retry_timeout = kwargs.get("timeout")
        kwargs["timeout"] = CFX_CONNECT_TIMEOUT_SECONDS
        if self._cfx_use_bluez_cache:
            # bleak-retry-connector's service collection cache is in-memory and
            # empty after a HA Core restart. BlueZ itself persists bonded GATT
            # objects, however, and can rebuild Bleak's collection from those
            # objects without waiting forever for ServicesResolved on the CFX5.
            kwargs["dangerous_use_bleak_cache"] = True
            _LOGGER.debug("Using BlueZ persisted GATT objects for bonded CFX reconnect")
        if getattr(self, "_pair_before_connect", False):
            _LOGGER.debug("Using native pre-connect bonding for the CFX")
        _LOGGER.debug(
            "CFX connection hook active; using %.1fs timeout instead of %s",
            CFX_CONNECT_TIMEOUT_SECONDS,
            retry_timeout,
        )
        await super().connect(**kwargs)
        _LOGGER.debug("CFX BLE link and GATT services are ready")
