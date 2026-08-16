"""Dometic DDM2 protocol used by CFX2 and CFX5 coolers.

The topic definitions and encodings in this module were derived from the
Mobile Cooling 2.0.32 application. DDM2 integers and structure members are
little-endian 32-bit values. Error arrays use little-endian 16-bit values.
"""

from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
import struct


class Action(IntEnum):
    """DDM2 action byte."""

    HELLO = 0x03
    ACK = 0x04
    NAK = 0x05
    NOP = 0x06
    PUBLISH = 0x10
    SET = 0x11
    SUBSCRIBE = 0x12
    FRAGMENT = 0x14


def _topic(parameter: int) -> bytes:
    """Build a Mobile Cooling Controller Class (mccc, class 0x1A) topic."""

    return bytes((parameter, 0x00, 0x00, 0x1A))


TOPIC_PRODUCT_TYPE = _topic(0x01)
TOPIC_COMPARTMENT_COUNT = _topic(0x02)
TOPIC_COMPARTMENT_POWER = _topic(0x03)
TOPIC_MEASURED_TEMPERATURE = _topic(0x04)
TOPIC_SET_TEMPERATURE = _topic(0x05)
TOPIC_ACTIVE_COMPARTMENT = _topic(0x06)
TOPIC_DOOR_OPEN = _topic(0x07)
TOPIC_TEMPERATURE_RANGE = _topic(0x08)
TOPIC_COOLER_POWER = _topic(0x0B)
TOPIC_VOLTAGE = _topic(0x0C)
TOPIC_BATTERY_PROTECTION = _topic(0x0D)
TOPIC_COMPRESSOR_POWER = _topic(0x0E)
TOPIC_CURRENT = _topic(0x0F)
TOPIC_POWER_SOURCE = _topic(0x10)
TOPIC_ICEMAKER_POWER = _topic(0x11)
TOPIC_ERRORS = _topic(0x12)
TOPIC_SERIAL_NUMBER = _topic(0x13)
TOPIC_SKU = _topic(0x14)
TOPIC_FIRMWARE_VERSION = _topic(0x15)
TOPIC_FIRMWARE_ID = bytes((0x07, 0x00, 0x01, 0x00))

# Product-info class 0x1C (distinct from the 0x1A realtime class). Holds the
# exact marketing product name and CMS SKU on-device, verified on a CFX5 25.
TOPIC_PRODUCT_NAME = bytes((0x01, 0x00, 0x00, 0x1C))
TOPIC_PRODUCT_SKU = bytes((0x03, 0x00, 0x00, 0x1C))

# These are the exact CFX2/CFX5 dashboard subscriptions in Mobile Cooling
# 2.0.32, plus battery protection from its settings screen and cfg.fwid for
# family detection. Valid but nonessential mccc diagnostics stay in the decoder
# and are deliberately not queried yet.
SUBSCRIPTIONS: tuple[bytes, ...] = (
    TOPIC_COOLER_POWER,
    TOPIC_SET_TEMPERATURE,
    TOPIC_MEASURED_TEMPERATURE,
    TOPIC_TEMPERATURE_RANGE,
    TOPIC_COMPARTMENT_POWER,
    TOPIC_ACTIVE_COMPARTMENT,
    TOPIC_VOLTAGE,
    TOPIC_CURRENT,
    TOPIC_POWER_SOURCE,
    TOPIC_ICEMAKER_POWER,
    TOPIC_DOOR_OPEN,
    TOPIC_ERRORS,
    TOPIC_PRODUCT_TYPE,
    TOPIC_COMPARTMENT_COUNT,
    TOPIC_BATTERY_PROTECTION,
    TOPIC_FIRMWARE_ID,
)

# Identity topics are read once after connecting (like the app), not
# subscribed, to keep the subscription set byte-identical to Mobile Cooling.
# Serial (0x13) and SKU/article (0x14) are in the 0x1A class; the exact
# product name and CMS SKU are in the 0x1C product-info class.
IDENTITY_READS: tuple[bytes, ...] = (
    TOPIC_SERIAL_NUMBER,
    TOPIC_SKU,
    TOPIC_PRODUCT_NAME,
    TOPIC_PRODUCT_SKU,
)

PRODUCT_TYPE_NAMES = {
    0: "Unconfigured",
    1: "Single Zone",
    2: "Single Zone with ice maker",
    3: "Dual Zone",
    4: "Deli Box",
}

# CMS SKU -> exact model name, from Dometic's own product data (firmwareId
# MC1 = the CFX5 family). Used as the cleanest offline model-name source.
_MODEL_BY_CMS_SKU = {
    "9620015957": "CFX5 25",
    "9620015958": "CFX5 35",
    "9620015959": "CFX5 45",
    "9620015960": "CFX5 55",
    "9620015961": "CFX5 55IM",
    "9620015962": "CFX5 75DZ",
    "9620015963": "CFX5 95DZ",
}


def _normalise_product_name(raw: str) -> str:
    """Turn the on-device product name into readable form.

    "CFX525" -> "CFX5 25", "CFX575DZ" -> "CFX5 75DZ". The name is a family
    prefix ("CFX" + one series digit) directly followed by the litre number;
    insert a space between them. Anything not matching is returned unchanged.
    """
    if len(raw) < 5 or not raw.startswith("CFX"):
        return raw
    if not (raw[3].isdigit() and raw[4].isdigit()):
        return raw
    return f"{raw[:4]} {raw[4:]}"

# Power source values. 0=AC and 1=DC are verified on real hardware; 2=Solar
# is an assumption (the app exposes a solar option, but the numeric value was
# never observed on a box). Unknown values fall through to their raw number.
POWER_SOURCE_NAMES = {0: "AC", 1: "DC", 2: "Solar"}
BATTERY_PROTECTION_NAMES = {0: "Low", 1: "Medium", 2: "High"}

ERROR_NAMES = {
    16: "DC input undervoltage",
    17: "DC input overvoltage",
    23: "Door open too long",
    26: "Solenoid valve fault",
    27: "Temperature out of range",
    512: "Compressor fan overcurrent",
    513: "Compressor failed to start",
    514: "Compressor speed too low",
    515: "Compressor overtemperature",
    516: "Compressor fan speed too low",
    517: "Compartment 1 NTC open circuit",
    518: "Compartment 1 NTC short circuit",
    519: "Compartment 2 NTC open circuit",
    520: "Compartment 2 NTC short circuit",
    521: "Compartment 3 NTC open circuit",
    522: "Compartment 3 NTC short circuit",
    523: "Temperature out of range",
    524: "Controller overtemperature",
}


class DeviceFamily(StrEnum):
    """CFX generations supported by Mobile Cooling 2.0.32."""

    UNKNOWN = "CFX"
    CFX2 = "CFX2"
    CFX3 = "CFX3"
    CFX5 = "CFX5"


def family_from_name(name: str | None) -> DeviceFamily:
    """Infer a family from Dometic's firmware/name prefix."""

    if not name:
        return DeviceFamily.UNKNOWN
    normalized = name.upper().split("_", 1)[0]
    if normalized.startswith(("MC2", "MC3", "CFX2")):
        return DeviceFamily.CFX2
    if normalized.startswith(("CFX3",)):
        return DeviceFamily.CFX3
    if normalized.startswith(("MC1", "CFX5")):
        return DeviceFamily.CFX5
    return DeviceFamily.UNKNOWN


@dataclass(slots=True)
class CFXState:
    """Latest values published by any supported CFX generation."""

    family: DeviceFamily = DeviceFamily.UNKNOWN
    firmware_id: str | None = None
    product_type: int | None = None
    compartment_count: int | None = None
    compartment_power: list[bool] = field(default_factory=list)
    measured_temperature: list[float] = field(default_factory=list)
    set_temperature: list[float] = field(default_factory=list)
    temperature_min: list[float] = field(default_factory=list)
    temperature_max: list[float] = field(default_factory=list)
    door_open: list[bool] = field(default_factory=list)
    active_compartment: int | None = None
    cooler_power: bool | None = None
    voltage: float | None = None
    battery_protection: int | None = None
    compressor_running: bool | None = None
    current: float | None = None
    power_source: int | None = None
    icemaker_power: bool | None = None
    errors: tuple[int, ...] = ()
    legacy_errors: tuple[str, ...] = ()
    alerts: tuple[str, ...] = ()
    serial_number: str | None = None
    sku: str | None = None
    # From the 0x1C product-info class: exact product name and CMS SKU.
    product_name: str | None = None
    cms_sku: str | None = None
    firmware_version: str | None = None

    @property
    def model_name(self) -> str:
        """Return the most exact readable model name.

        Resolution order, matching the ESPHome component:
        1. CMS SKU (from 0x1C) mapped to Dometic's exact name via a table.
        2. On-device product name (from 0x1C), normalised ("CFX525" ->
           "CFX5 25"). This is what real firmware usually provides.
        3. Derived from family + product type ("CFX5 Single Zone").

        Note: the 0x14 topic (self.sku) is a serial/article number on at
        least some boxes, so it is not used as a model name.
        """
        from_sku = _MODEL_BY_CMS_SKU.get(self.cms_sku or "")
        if from_sku:
            return from_sku
        if self.product_name:
            return _normalise_product_name(self.product_name)
        family = self.family.value
        if self.product_type is None:
            return family
        product = PRODUCT_TYPE_NAMES.get(self.product_type, f"type {self.product_type}")
        return f"{family} {product}"

    @property
    def product_type_name(self) -> str | None:
        """Return the readable product type (Single Zone, Dual Zone, ...)."""
        if self.product_type is None:
            return None
        return PRODUCT_TYPE_NAMES.get(self.product_type, f"type {self.product_type}")

    @property
    def power_source_name(self) -> str | None:
        """Return the readable power source."""

        if self.power_source is None:
            return None
        return POWER_SOURCE_NAMES.get(self.power_source, str(self.power_source))

    @property
    def battery_protection_name(self) -> str | None:
        """Return the readable battery protection setting."""

        if self.battery_protection is None:
            return None
        return BATTERY_PROTECTION_NAMES.get(
            self.battery_protection, str(self.battery_protection)
        )

    @property
    def error_text(self) -> str:
        """Return all active errors as text."""

        descriptions = [
            ERROR_NAMES.get(code, f"Unknown error 0x{code:04X}") for code in self.errors
        ]
        descriptions.extend(self.legacy_errors)
        descriptions.extend(self.alerts)
        if not descriptions:
            return "No errors"
        return ", ".join(descriptions)

    @property
    def has_problem(self) -> bool:
        """Return whether an error or alert is active."""

        return bool(self.errors or self.legacy_errors or self.alerts)


# Compatibility alias for the first CFX5-only release.
CFX5State = CFXState


def subscribe_frame(topic: bytes) -> bytes:
    """Build a subscribe frame."""

    if len(topic) != 4:
        raise ValueError("A DDM2 topic must contain four bytes")
    return bytes((Action.SUBSCRIBE,)) + topic


def set_frame(topic: bytes, payload: bytes) -> bytes:
    """Build a set frame."""

    if len(topic) != 4:
        raise ValueError("A DDM2 topic must contain four bytes")
    return bytes((Action.SET,)) + topic + payload


def encode_int32(value: int | bool) -> bytes:
    """Encode a DDM2 INT32_T value."""

    return struct.pack("<i", int(value))


def encode_millivalue(value: float) -> bytes:
    """Encode a value using Dometic's factor-1000 representation."""

    return encode_int32(round(value * 1000))


def encode_int32_array(values: list[int | bool] | tuple[int | bool, ...]) -> bytes:
    """Encode a DDM2 structure containing an i32 array."""

    return b"".join(encode_int32(value) for value in values)


def encode_millivalue_array(values: list[float] | tuple[float, ...]) -> bytes:
    """Encode a DDM2 structure containing a factor-1000 i32 array."""

    return b"".join(encode_millivalue(value) for value in values)


def _decode_int32(payload: bytes) -> int | None:
    if len(payload) < 4:
        return None
    return struct.unpack_from("<i", payload)[0]


def _decode_int32_array(payload: bytes) -> list[int]:
    usable_length = len(payload) - (len(payload) % 4)
    return [value[0] for value in struct.iter_unpack("<i", payload[:usable_length])]


def _decode_string(payload: bytes) -> str:
    return payload.split(b"\x00", 1)[0].decode("utf-8", errors="replace")


def _set_detected_compartment_count(state: CFXState) -> None:
    if state.compartment_count is not None:
        return
    if state.product_type == 3:
        state.compartment_count = 2
    elif state.product_type in (0, 1, 2, 4):
        state.compartment_count = 1


def parse_publish(  # noqa: C901
    frame: bytes | bytearray, state: CFXState
) -> bool:
    """Apply one DDM2 publish frame to state.

    Return True when the frame was a recognized publish and had a valid payload.
    """

    data = bytes(frame)
    if len(data) < 5 or data[0] != Action.PUBLISH:
        return False

    topic = data[1:5]
    payload = data[5:]

    if topic == TOPIC_PRODUCT_TYPE:
        if (value := _decode_int32(payload)) is None:
            return False
        state.product_type = value
        _set_detected_compartment_count(state)
    elif topic == TOPIC_COMPARTMENT_COUNT:
        if (value := _decode_int32(payload)) is None or value < 1:
            return False
        state.compartment_count = value
    elif topic == TOPIC_COMPARTMENT_POWER:
        values = _decode_int32_array(payload)
        if not values:
            return False
        state.compartment_power = [bool(value) for value in values]
    elif topic == TOPIC_MEASURED_TEMPERATURE:
        values = _decode_int32_array(payload)
        if not values:
            return False
        state.measured_temperature = [value / 1000 for value in values]
    elif topic == TOPIC_SET_TEMPERATURE:
        values = _decode_int32_array(payload)
        if not values:
            return False
        state.set_temperature = [value / 1000 for value in values]
    elif topic == TOPIC_TEMPERATURE_RANGE:
        values = _decode_int32_array(payload)
        if len(values) < 2:
            return False
        state.temperature_min = [value / 1000 for value in values[0::2]]
        state.temperature_max = [value / 1000 for value in values[1::2]]
    elif topic == TOPIC_DOOR_OPEN:
        values = _decode_int32_array(payload)
        if not values:
            return False
        state.door_open = [bool(value) for value in values]
    elif topic == TOPIC_ACTIVE_COMPARTMENT:
        if (value := _decode_int32(payload)) is None:
            return False
        state.active_compartment = value
    elif topic == TOPIC_COOLER_POWER:
        if (value := _decode_int32(payload)) is None:
            return False
        state.cooler_power = bool(value)
    elif topic == TOPIC_VOLTAGE:
        if (value := _decode_int32(payload)) is None:
            return False
        state.voltage = value / 1000
    elif topic == TOPIC_BATTERY_PROTECTION:
        if (value := _decode_int32(payload)) is None:
            return False
        state.battery_protection = value
    elif topic == TOPIC_COMPRESSOR_POWER:
        if (value := _decode_int32(payload)) is None:
            return False
        state.compressor_running = bool(value)
    elif topic == TOPIC_CURRENT:
        if (value := _decode_int32(payload)) is None:
            return False
        state.current = value / 1000
    elif topic == TOPIC_POWER_SOURCE:
        if (value := _decode_int32(payload)) is None:
            return False
        state.power_source = value
    elif topic == TOPIC_ICEMAKER_POWER:
        if (value := _decode_int32(payload)) is None:
            return False
        state.icemaker_power = bool(value)
    elif topic == TOPIC_ERRORS:
        usable_length = len(payload) - (len(payload) % 2)
        state.errors = tuple(
            value[0]
            for value in struct.iter_unpack("<H", payload[:usable_length])
            if value[0] != 0
        )
    elif topic == TOPIC_SERIAL_NUMBER:
        state.serial_number = _decode_string(payload)
    elif topic == TOPIC_SKU:
        state.sku = _decode_string(payload)
    elif topic == TOPIC_PRODUCT_NAME:
        state.product_name = _decode_string(payload)
    elif topic == TOPIC_PRODUCT_SKU:
        state.cms_sku = _decode_string(payload)
    elif topic == TOPIC_FIRMWARE_VERSION:
        state.firmware_version = _decode_string(payload)
    elif topic == TOPIC_FIRMWARE_ID:
        state.firmware_id = _decode_string(payload)
        detected_family = family_from_name(state.firmware_id)
        if detected_family is not DeviceFamily.UNKNOWN:
            state.family = detected_family
    else:
        return False

    return True
