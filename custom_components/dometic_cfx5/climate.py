"""Climate entities for Dometic CFX compartments."""

from typing import Any, ClassVar

from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import ClimateEntityFeature, HVACMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import DometicCFXCoordinator
from .entity import DometicCFXEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create one climate entity per detected compartment."""

    coordinator: DometicCFXCoordinator = entry.runtime_data
    count = coordinator.data.compartment_count or 1
    async_add_entities(DometicCFXClimate(coordinator, index) for index in range(count))


class DometicCFXClimate(DometicCFXEntity, ClimateEntity):
    """A temperature-controlled CFX compartment."""

    _attr_hvac_modes: ClassVar[list[HVACMode]] = [HVACMode.OFF, HVACMode.COOL]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_OFF
        | ClimateEntityFeature.TURN_ON
    )
    _attr_target_temperature_step = 1.0
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_translation_key = "compartment"

    def __init__(self, coordinator: DometicCFXCoordinator, index: int) -> None:
        """Initialize a compartment."""

        super().__init__(coordinator, f"compartment_{index}")
        self._index = index
        self._attr_translation_placeholders = {"number": str(index + 1)}

    @property
    def available(self) -> bool:
        """Require the three values needed by a climate entity."""

        state = self.coordinator.data
        return (
            super().available
            and len(state.compartment_power) > self._index
            and len(state.measured_temperature) > self._index
            and len(state.set_temperature) > self._index
            and state.compartment_power[self._index] is not None
            and state.measured_temperature[self._index] is not None
            and state.set_temperature[self._index] is not None
        )

    @property
    def current_temperature(self) -> float | None:
        """Return the measured temperature."""

        values = self.coordinator.data.measured_temperature
        return values[self._index] if len(values) > self._index else None

    @property
    def target_temperature(self) -> float | None:
        """Return the target temperature."""

        values = self.coordinator.data.set_temperature
        return values[self._index] if len(values) > self._index else None

    @property
    def min_temp(self) -> float:
        """Return the device-published lower limit."""

        values = self.coordinator.data.temperature_min
        return values[self._index] if len(values) > self._index else -22.0

    @property
    def max_temp(self) -> float:
        """Return the device-published upper limit."""

        values = self.coordinator.data.temperature_max
        return values[self._index] if len(values) > self._index else 20.0

    @property
    def hvac_mode(self) -> HVACMode:
        """Return COOL only when global and compartment power are enabled."""

        state = self.coordinator.data
        compartment_on = (
            len(state.compartment_power) > self._index
            and state.compartment_power[self._index]
        )
        return (
            HVACMode.COOL
            if state.cooler_power is not False and compartment_on
            else HVACMode.OFF
        )

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Switch this compartment on or off."""

        await self.coordinator.async_set_compartment_power(
            self._index, hvac_mode != HVACMode.OFF
        )

    async def async_turn_on(self) -> None:
        """Turn this compartment on."""

        await self.coordinator.async_set_compartment_power(self._index, True)

    async def async_turn_off(self) -> None:
        """Turn this compartment off."""

        await self.coordinator.async_set_compartment_power(self._index, False)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set this compartment's target temperature."""

        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is not None:
            await self.coordinator.async_set_temperature(
                self._index, float(temperature)
            )
