"""Native Home Assistant integration for Dometic CFX coolers."""

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth.match import ADDRESS, BluetoothCallbackMatcher
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant, callback

from .const import PLATFORMS
from .coordinator import DometicCFXCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a discovered CFX2, CFX3 or CFX5."""

    coordinator = DometicCFXCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    @callback
    def _async_seen_again(
        _service_info: bluetooth.BluetoothServiceInfoBleak,
        _change: bluetooth.BluetoothChange,
    ) -> None:
        """Reconnect promptly when a disconnected CFX is seen again."""

        if not coordinator.connected:
            hass.async_create_task(
                coordinator.async_request_refresh(),
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

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a CFX config entry."""

    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.async_shutdown()
    return unload_ok
