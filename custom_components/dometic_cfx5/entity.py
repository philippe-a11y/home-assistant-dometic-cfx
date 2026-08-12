"""Base entity for Dometic CFX."""

from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import DometicCFXCoordinator


class DometicCFXEntity(CoordinatorEntity[DometicCFXCoordinator]):
    """Common CFX entity behavior."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: DometicCFXCoordinator, key: str) -> None:
        """Initialize the entity."""

        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address}_{key}"

    @property
    def device_info(self) -> DeviceInfo:
        """Describe the physical cooler."""

        state = self.coordinator.data
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.address)},
            connections={(CONNECTION_BLUETOOTH, self.coordinator.address)},
            manufacturer="Dometic",
            name=self.coordinator.config_entry.title,
            model=state.model_name,
            model_id=state.sku,
            serial_number=state.serial_number,
            sw_version=state.firmware_version,
        )

    @property
    def available(self) -> bool:
        """Return availability from the persistent Bluetooth link."""

        return super().available and self.coordinator.connected
