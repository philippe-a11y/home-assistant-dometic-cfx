"""Constants for the Dometic CFX integration."""

from homeassistant.const import Platform

DOMAIN = "dometic_cfx5"

DDM1_SERVICE_UUID = "537a0300-0995-481f-926c-1604e23fd515"
DDM1_WRITE_UUID = "537a0301-0995-481f-926c-1604e23fd515"
DDM1_NOTIFY_UUID = "537a0302-0995-481f-926c-1604e23fd515"

DDM2_SERVICE_UUID = "537a0400-0995-481f-926c-1604e23fd515"
DDM2_WRITE_UUID = "537a0401-0995-481f-926c-1604e23fd515"
DDM2_NOTIFY_UUID = "537a0402-0995-481f-926c-1604e23fd515"

SERVICE_UUIDS = frozenset((DDM1_SERVICE_UUID, DDM2_SERVICE_UUID))

PLATFORMS: tuple[Platform, ...] = (
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CLIMATE,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
)

DEFAULT_NAME = "Dometic CFX"
UPDATE_INTERVAL_SECONDS = 30
INITIAL_DATA_TIMEOUT_SECONDS = 25

# Bluetooth source selection. CONF_SOURCE holds either SOURCE_AUTO (let HA
# pick the nearest reachable adapter/proxy) or a specific scanner source MAC
# (a proxy or a local adapter) to force connections through only that source.
CONF_SOURCE = "source"
SOURCE_AUTO = "auto"
