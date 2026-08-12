"""Switches for Dometic CFX."""

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import DometicCFXCoordinator
from .entity import DometicCFXEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create CFX switches."""

    coordinator: DometicCFXCoordinator = entry.runtime_data
    entities: list[SwitchEntity] = [DometicCFXCoolerPowerSwitch(coordinator)]
    if coordinator.data.product_type == 2:
        entities.append(DometicCFXIceMakerSwitch(coordinator))
    async_add_entities(entities)


class DometicCFXCoolerPowerSwitch(DometicCFXEntity, SwitchEntity):
    """Global cooler power."""

    _attr_translation_key = "cooler_power"

    def __init__(self, coordinator: DometicCFXCoordinator) -> None:
        """Initialize the power switch."""

        super().__init__(coordinator, "cooler_power")

    @property
    def is_on(self) -> bool | None:
        """Return the global power state."""

        return self.coordinator.data.cooler_power

    async def async_turn_on(self, **kwargs: object) -> None:
        """Turn the cooler on."""

        await self.coordinator.async_set_cooler_power(True)

    async def async_turn_off(self, **kwargs: object) -> None:
        """Turn the cooler off."""

        await self.coordinator.async_set_cooler_power(False)


class DometicCFXIceMakerSwitch(DometicCFXEntity, SwitchEntity):
    """Ice maker power on supported models."""

    _attr_translation_key = "ice_maker"

    def __init__(self, coordinator: DometicCFXCoordinator) -> None:
        """Initialize the ice maker switch."""

        super().__init__(coordinator, "ice_maker")

    @property
    def is_on(self) -> bool | None:
        """Return ice maker power."""

        return self.coordinator.data.icemaker_power

    async def async_turn_on(self, **kwargs: object) -> None:
        """Turn the ice maker on."""

        await self.coordinator.async_set_icemaker_power(True)

    async def async_turn_off(self, **kwargs: object) -> None:
        """Turn the ice maker off."""

        await self.coordinator.async_set_icemaker_power(False)
