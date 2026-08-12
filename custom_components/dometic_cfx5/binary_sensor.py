"""Binary sensors for Dometic CFX."""

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
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
    """Create CFX binary sensors."""

    coordinator: DometicCFXCoordinator = entry.runtime_data
    count = coordinator.data.compartment_count or 1
    entities: list[BinarySensorEntity] = [
        DometicCFXProblemSensor(coordinator),
    ]
    entities.extend(DometicCFXDoorSensor(coordinator, index) for index in range(count))
    async_add_entities(entities)


class DometicCFXDoorSensor(DometicCFXEntity, BinarySensorEntity):
    """Door state for one compartment."""

    _attr_device_class = BinarySensorDeviceClass.DOOR
    _attr_translation_key = "compartment_door"

    def __init__(self, coordinator: DometicCFXCoordinator, index: int) -> None:
        """Initialize a door sensor."""

        super().__init__(coordinator, f"door_{index}")
        self._index = index
        self._attr_translation_placeholders = {"number": str(index + 1)}

    @property
    def is_on(self) -> bool | None:
        """Return whether the door is open."""

        values = self.coordinator.data.door_open
        return values[self._index] if len(values) > self._index else None


class DometicCFXProblemSensor(DometicCFXEntity, BinarySensorEntity):
    """Combined device problem sensor."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_translation_key = "problem"

    def __init__(self, coordinator: DometicCFXCoordinator) -> None:
        """Initialize the problem sensor."""

        super().__init__(coordinator, "problem")

    @property
    def is_on(self) -> bool:
        """Return whether the cooler publishes any active error."""

        return self.coordinator.data.has_problem

    @property
    def extra_state_attributes(self) -> dict[str, str | list[int] | list[str]]:
        """Expose decoded and raw errors."""

        return {
            "errors": list(self.coordinator.data.errors),
            "legacy_errors": list(self.coordinator.data.legacy_errors),
            "alerts": list(self.coordinator.data.alerts),
            "description": self.coordinator.data.error_text,
        }
