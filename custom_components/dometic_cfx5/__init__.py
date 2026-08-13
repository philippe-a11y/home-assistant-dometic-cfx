"""Native Home Assistant integration for Dometic CFX coolers."""

import asyncio
import logging

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth.match import ADDRESS, BluetoothCallbackMatcher
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, EVENT_HOMEASSISTANT_STOP
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady

from .bluez_pairing import async_remove_cfx_bluez_bond
from .const import PLATFORMS
from .coordinator import DometicCFXCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a discovered CFX2, CFX3 or CFX5."""

    coordinator = DometicCFXCoordinator(hass, entry)
    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryNotReady as err:
        # Load the entry anyway. The re-pair button must exist precisely when
        # the first connection fails (stale bond, cooler not in pairing mode,
        # cooler asleep). All other entities stay unavailable until the
        # Bluetooth seen-again callback or the regular poll reconnects.
        _LOGGER.warning(
            "CFX %s is not reachable yet, loading anyway so the re-pair "
            "button is available: %s",
            entry.data[CONF_ADDRESS],
            err,
        )
    entry.runtime_data = coordinator
    reconnect_task: asyncio.Task[None] | None = None

    async def _async_reconnect() -> None:
        """Run one coalesced reconnect request."""

        await coordinator.async_request_refresh()

    @callback
    def _async_seen_again(
        _service_info: bluetooth.BluetoothServiceInfoBleak,
        _change: bluetooth.BluetoothChange,
    ) -> None:
        """Reconnect promptly when a disconnected CFX is seen again."""

        nonlocal reconnect_task
        if not coordinator.connected and (
            reconnect_task is None or reconnect_task.done()
        ):
            reconnect_task = hass.async_create_task(
                _async_reconnect(),
                f"Reconnect Dometic CFX {coordinator.address}",
            )

    entry.async_on_unload(
        bluetooth.async_register_callback(
            hass,
            _async_seen_again,
            BluetoothCallbackMatcher({ADDRESS: entry.data[CONF_ADDRESS]}),
            bluetooth.BluetoothScanningMode.ACTIVE,
        )
    )

    async def _async_disconnect_on_stop(_event: Event) -> None:
        """Release the BLE connection when Home Assistant shuts down.

        Without a clean disconnect the cooler keeps believing the old
        session is alive until its link supervision timeout expires, stops
        advertising in the meantime, and is unreachable for the first
        minutes after a restart.
        """

        await coordinator.async_shutdown()

    entry.async_on_unload(
        hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_STOP, _async_disconnect_on_stop
        )
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a CFX config entry."""

    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.async_shutdown()
    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove the local BlueZ bond when the user deletes a CFX entry.

    Removing and rediscovering the entry is the explicit repair operation for
    a stale CFX bond. It leaves bonds on phones and other Bluetooth adapters
    untouched.
    """

    await async_remove_cfx_bluez_bond(entry.data[CONF_ADDRESS])
