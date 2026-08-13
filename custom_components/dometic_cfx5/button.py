"""Buttons for Dometic CFX."""

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import DometicCFXCoordinator
from .entity import DometicCFXEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create CFX buttons."""

    coordinator: DometicCFXCoordinator = entry.runtime_data
    async_add_entities([DometicCFXRepairBondButton(coordinator)])


class DometicCFXRepairBondButton(DometicCFXEntity, ButtonEntity):
    """Remove the stale local bond and pair the CFX again."""

    _attr_translation_key = "repair_bond"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: DometicCFXCoordinator) -> None:
        """Initialize the re-pair button."""

        super().__init__(coordinator, "repair_bond")

    @property
    def available(self) -> bool:
        """Stay available even while the CFX connection is down.

        The whole purpose of this button is recovering a connection that
        cannot be established, so it must not depend on being connected.
        """

        return True

    async def async_press(self) -> None:
        """Remove the local bond and trigger fresh pairing."""

        await self.coordinator.async_repair_bond()
