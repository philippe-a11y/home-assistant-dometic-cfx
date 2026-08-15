"""Sensors for Dometic CFX."""

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import DometicCFXCoordinator
from .entity import DometicCFXEntity
from .protocol import CFXState


@dataclass(frozen=True, kw_only=True)
class SensorDescription:
    """Description of a scalar CFX sensor."""

    key: str
    translation_key: str
    value_fn: Callable[[CFXState], float | int | None]
    device_class: SensorDeviceClass | None = None
    native_unit: str | None = None


SENSORS = (
    SensorDescription(
        key="voltage",
        translation_key="voltage",
        value_fn=lambda state: state.voltage,
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit=UnitOfElectricPotential.VOLT,
    ),
    SensorDescription(
        key="current",
        translation_key="current",
        value_fn=lambda state: state.current,
        device_class=SensorDeviceClass.CURRENT,
        native_unit=UnitOfElectricCurrent.AMPERE,
    ),
    SensorDescription(
        key="power",
        translation_key="power",
        value_fn=lambda state: (
            round(state.voltage * state.current, 1)
            if state.voltage is not None and state.current is not None
            else None
        ),
        device_class=SensorDeviceClass.POWER,
        native_unit=UnitOfPower.WATT,
    ),
)


@dataclass(frozen=True, kw_only=True)
class TextSensorDescription:
    """Description of a textual CFX sensor."""

    key: str
    translation_key: str
    value_fn: Callable[[CFXState], str | None]


TEXT_SENSORS = (
    TextSensorDescription(
        key="product_type",
        translation_key="product_type",
        value_fn=lambda state: state.model_name,
    ),
    TextSensorDescription(
        key="power_source",
        translation_key="power_source",
        value_fn=lambda state: state.power_source_name,
    ),
    TextSensorDescription(
        key="active_errors",
        translation_key="active_errors",
        value_fn=lambda state: state.error_text,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create CFX sensors."""

    coordinator: DometicCFXCoordinator = entry.runtime_data
    count = coordinator.data.compartment_count or 1
    entities: list[SensorEntity] = [
        DometicCFXSensor(coordinator, description) for description in SENSORS
    ]
    entities.extend(
        DometicCFXTextSensor(coordinator, description) for description in TEXT_SENSORS
    )
    entities.extend(
        DometicCFXTemperatureSensor(coordinator, index) for index in range(count)
    )
    async_add_entities(entities)


class DometicCFXSensor(DometicCFXEntity, SensorEntity):
    """A scalar CFX sensor."""

    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: DometicCFXCoordinator,
        description: SensorDescription,
    ) -> None:
        """Initialize a scalar sensor."""

        super().__init__(coordinator, description.key)
        self._description = description
        self._attr_translation_key = description.translation_key
        self._attr_device_class = description.device_class
        self._attr_native_unit_of_measurement = description.native_unit

    @property
    def native_value(self) -> float | int | None:
        """Return the latest value."""

        return self._description.value_fn(self.coordinator.data)


class DometicCFXTemperatureSensor(DometicCFXEntity, SensorEntity):
    """Measured temperature for one compartment.

    The same temperature is already shown by that compartment's climate
    entity (current_temperature), so this standalone sensor is disabled by
    default to avoid duplication. It stays available for users who want a
    dedicated sensor for history graphs and can enable it in the UI.
    """

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_translation_key = "compartment_temperature"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: DometicCFXCoordinator, index: int) -> None:
        """Initialize a compartment temperature sensor."""

        super().__init__(coordinator, f"temperature_{index}")
        self._index = index
        self._attr_translation_placeholders = {"number": str(index + 1)}

    @property
    def native_value(self) -> float | None:
        """Return the measured compartment temperature."""

        values = self.coordinator.data.measured_temperature
        return values[self._index] if len(values) > self._index else None


class DometicCFXTextSensor(DometicCFXEntity, SensorEntity):
    """A textual CFX sensor."""

    def __init__(
        self,
        coordinator: DometicCFXCoordinator,
        description: TextSensorDescription,
    ) -> None:
        """Initialize a textual sensor."""

        super().__init__(coordinator, description.key)
        self._description = description
        self._attr_translation_key = description.translation_key

    @property
    def native_value(self) -> str | None:
        """Return the current text value."""

        return self._description.value_fn(self.coordinator.data)
