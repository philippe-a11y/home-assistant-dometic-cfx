"""Select entities for Dometic CFX."""

from typing import ClassVar

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import DometicCFXCoordinator
from .entity import DometicCFXEntity
from .protocol import BATTERY_PROTECTION_NAMES

OPTION_TO_LEVEL = {name: level for level, name in BATTERY_PROTECTION_NAMES.items()}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create CFX select entities."""

    coordinator: DometicCFXCoordinator = entry.runtime_data
    async_add_entities([DometicCFXBatteryProtectionSelect(coordinator)])


class DometicCFXBatteryProtectionSelect(DometicCFXEntity, SelectEntity):
    """DC battery protection threshold."""

    _attr_translation_key = "battery_protection"
    _attr_options: ClassVar[list[str]] = list(OPTION_TO_LEVEL)

    def __init__(self, coordinator: DometicCFXCoordinator) -> None:
        """Initialize the select."""

        super().__init__(coordinator, "battery_protection")

    @property
    def current_option(self) -> str | None:
        """Return the current protection level."""

        return self.coordinator.data.battery_protection_name

    async def async_select_option(self, option: str) -> None:
        """Set the selected protection level."""

        if option not in OPTION_TO_LEVEL:
            raise HomeAssistantError(f"Invalid battery protection option {option}")
        await self.coordinator.async_set_battery_protection(OPTION_TO_LEVEL[option])
