"""DDM1 protocol used by Dometic CFX3 coolers.

The topics, dashboard subscription set, handshake and value encodings here are
derived from Mobile Cooling 2.0.32. DDM1 uses individual topics per compartment
instead of DDM2's arrays.
"""

from enum import IntEnum
import struct

from .protocol import CFXState, DeviceFamily


class Action(IntEnum):
    """DDM1 action byte."""

    PUBLISH = 0x00
    SUBSCRIBE = 0x01
    PING = 0x02
    HELLO = 0x03
    ACK = 0x04
    NAK = 0x05
    NOP = 0x06


TOPIC_PRODUCT_MODEL = bytes((0x00, 0xC0, 0x00, 0x00))
TOPIC_SERIAL_NUMBER = bytes((0x00, 0xC1, 0x00, 0x00))
TOPIC_PRODUCT_TYPE = bytes((0x00, 0xC4, 0x00, 0x00))
TOPIC_HINGE_POSITION = bytes((0x00, 0xC5, 0x00, 0x00))

TOPIC_COMPARTMENT_POWER = (
    bytes((0x00, 0x00, 0x01, 0x01)),
    bytes((0x10, 0x00, 0x01, 0x01)),
)
TOPIC_MEASURED_TEMPERATURE = (
    bytes((0x00, 0x01, 0x01, 0x01)),
    bytes((0x10, 0x01, 0x01, 0x01)),
)
TOPIC_SET_TEMPERATURE = (
    bytes((0x00, 0x02, 0x01, 0x01)),
    bytes((0x10, 0x02, 0x01, 0x01)),
)
TOPIC_TEMPERATURE_RANGE = (
    bytes((0x00, 0x80, 0x01, 0x01)),
    bytes((0x10, 0x80, 0x01, 0x01)),
)
TOPIC_DOOR_OPEN = (
    bytes((0x00, 0x08, 0x01, 0x01)),
    bytes((0x10, 0x08, 0x01, 0x01)),
)

TOPIC_COOLER_POWER = bytes((0x00, 0x00, 0x03, 0x01))
TOPIC_VOLTAGE = bytes((0x00, 0x01, 0x03, 0x01))
TOPIC_BATTERY_PROTECTION = bytes((0x00, 0x02, 0x03, 0x01))
TOPIC_POWER_SOURCE = bytes((0x00, 0x05, 0x03, 0x01))
TOPIC_ICEMAKER_POWER = bytes((0x00, 0x06, 0x03, 0x01))

ERROR_TOPICS = {
    bytes((0x00, 0x01, 0x04, 0x01)): "Compartment 1 NTC open circuit",
    bytes((0x00, 0x02, 0x04, 0x01)): "Compartment 1 NTC short circuit",
    bytes((0x10, 0x01, 0x04, 0x01)): "Compartment 2 NTC open circuit",
    bytes((0x10, 0x02, 0x04, 0x01)): "Compartment 2 NTC short circuit",
    bytes((0x00, 0x34, 0x04, 0x01)): "Compressor speed too low",
    bytes((0x00, 0x33, 0x04, 0x01)): "Compressor failed to start",
    bytes((0x00, 0x03, 0x04, 0x01)): "Communication fault",
    bytes((0x00, 0x35, 0x04, 0x01)): "Controller overtemperature",
    bytes((0x00, 0x32, 0x04, 0x01)): "Compressor fan overcurrent",
    bytes((0x00, 0x14, 0x04, 0x01)): "Inner fan fault",
    bytes((0x00, 0x09, 0x04, 0x01)): "Solenoid valve fault",
}

ALERT_TOPICS = {
    bytes((0x00, 0x00, 0x05, 0x01)): "Temperature alert",
    bytes((0x00, 0x01, 0x05, 0x01)): "Door open too long",
    bytes((0x00, 0x02, 0x05, 0x01)): "Voltage alert",
    bytes((0x00, 0x03, 0x05, 0x01)): "DC module temperature alert",
}

# Exact CFX3 dashboard set from the app, plus battery protection from settings.
SUBSCRIPTIONS: tuple[bytes, ...] = (
    TOPIC_COMPARTMENT_POWER[0],
    TOPIC_SET_TEMPERATURE[0],
    TOPIC_MEASURED_TEMPERATURE[0],
    TOPIC_TEMPERATURE_RANGE[0],
    TOPIC_COMPARTMENT_POWER[1],
    TOPIC_SET_TEMPERATURE[1],
    TOPIC_MEASURED_TEMPERATURE[1],
    TOPIC_TEMPERATURE_RANGE[1],
    TOPIC_DOOR_OPEN[0],
    TOPIC_DOOR_OPEN[1],
    TOPIC_COOLER_POWER,
    TOPIC_VOLTAGE,
    TOPIC_ICEMAKER_POWER,
    TOPIC_POWER_SOURCE,
    *ERROR_TOPICS,
    bytes((0x00, 0x03, 0x05, 0x01)),
    bytes((0x00, 0x00, 0x05, 0x01)),
    bytes((0x00, 0x01, 0x05, 0x01)),
    bytes((0x00, 0x02, 0x05, 0x01)),
    TOPIC_PRODUCT_MODEL,
    TOPIC_SERIAL_NUMBER,
    TOPIC_PRODUCT_TYPE,
    TOPIC_HINGE_POSITION,
    TOPIC_BATTERY_PROTECTION,
)


def subscribe_frame(topic: bytes) -> bytes:
    """Build a DDM1 subscribe frame."""

    if len(topic) != 4:
        raise ValueError("A DDM1 topic must contain four bytes")
    return bytes((Action.SUBSCRIBE,)) + topic


def set_frame(topic: bytes, payload: bytes) -> bytes:
    """Build a DDM1 publish, which is also the write action."""

    if len(topic) != 4:
        raise ValueError("A DDM1 topic must contain four bytes")
    return bytes((Action.PUBLISH,)) + topic + payload


def encode_bool(value: bool) -> bytes:
    """Encode a DDM1 INT8_BOOLEAN."""

    return bytes((int(value),))


def encode_uint8(value: int) -> bytes:
    """Encode a DDM1 UINT8/INT8 enum."""

    if not 0 <= value <= 0xFF:
        raise ValueError("DDM1 byte value is out of range")
    return bytes((value,))


def encode_temperature(value: float) -> bytes:
    """Encode signed little-endian deci-degrees Celsius."""

    return struct.pack("<h", round(value * 10))


def _decode_bool(payload: bytes) -> bool | None:
    return bool(payload[0]) if payload else None


def _decode_temperature(payload: bytes) -> float | None:
    if len(payload) < 2:
        return None
    return struct.unpack_from("<h", payload)[0] / 10


def _decode_range(payload: bytes) -> tuple[float, float] | None:
    if len(payload) < 4:
        return None
    low, high = struct.unpack_from("<hh", payload)
    return low / 10, high / 10


def _decode_string(payload: bytes) -> str:
    return payload[:15].split(b"\x00", 1)[0].decode("utf-8", errors="replace")


def _set_slot(values: list, index: int, value: object) -> None:
    """Set an individual DDM1 compartment value, retaining zone order."""

    while len(values) <= index:
        values.append(None)
    values[index] = value


def _toggle_named_value(
    values: tuple[str, ...], name: str, enabled: bool
) -> tuple[str, ...]:
    current = set(values)
    if enabled:
        current.add(name)
    else:
        current.discard(name)
    return tuple(sorted(current))


def _set_compartment_count(state: CFXState) -> None:
    if state.product_type == 3:
        state.compartment_count = 2
    elif state.product_type in (0, 1, 2, 4):
        state.compartment_count = 1


def parse_publish(  # noqa: C901
    frame: bytes | bytearray, state: CFXState
) -> bool:
    """Apply one DDM1 publish frame to the shared CFX state."""

    data = bytes(frame)
    if len(data) < 5 or data[0] != Action.PUBLISH:
        return False
    topic = data[1:5]
    payload = data[5:]
    state.family = DeviceFamily.CFX3

    if topic == TOPIC_PRODUCT_MODEL:
        state.sku = _decode_string(payload)
    elif topic == TOPIC_SERIAL_NUMBER:
        state.serial_number = _decode_string(payload)
    elif topic == TOPIC_PRODUCT_TYPE:
        if not payload:
            return False
        state.product_type = payload[0]
        _set_compartment_count(state)
    elif topic == TOPIC_HINGE_POSITION:
        return bool(payload)
    elif topic in TOPIC_COMPARTMENT_POWER:
        if (value := _decode_bool(payload)) is None:
            return False
        _set_slot(state.compartment_power, TOPIC_COMPARTMENT_POWER.index(topic), value)
    elif topic in TOPIC_MEASURED_TEMPERATURE:
        if (value := _decode_temperature(payload)) is None:
            return False
        _set_slot(
            state.measured_temperature,
            TOPIC_MEASURED_TEMPERATURE.index(topic),
            value,
        )
    elif topic in TOPIC_SET_TEMPERATURE:
        if (value := _decode_temperature(payload)) is None:
            return False
        _set_slot(state.set_temperature, TOPIC_SET_TEMPERATURE.index(topic), value)
    elif topic in TOPIC_TEMPERATURE_RANGE:
        if (value := _decode_range(payload)) is None:
            return False
        index = TOPIC_TEMPERATURE_RANGE.index(topic)
        _set_slot(state.temperature_min, index, value[0])
        _set_slot(state.temperature_max, index, value[1])
    elif topic in TOPIC_DOOR_OPEN:
        if (value := _decode_bool(payload)) is None:
            return False
        _set_slot(state.door_open, TOPIC_DOOR_OPEN.index(topic), value)
    elif topic == TOPIC_COOLER_POWER:
        if (value := _decode_bool(payload)) is None:
            return False
        state.cooler_power = value
    elif topic == TOPIC_VOLTAGE:
        if len(payload) < 2:
            return False
        state.voltage = struct.unpack_from("<H", payload)[0] / 10
    elif topic == TOPIC_BATTERY_PROTECTION:
        if not payload:
            return False
        state.battery_protection = payload[0]
    elif topic == TOPIC_POWER_SOURCE:
        if not payload:
            return False
        state.power_source = payload[0]
    elif topic == TOPIC_ICEMAKER_POWER:
        if (value := _decode_bool(payload)) is None:
            return False
        state.icemaker_power = value
    elif topic in ERROR_TOPICS:
        if (value := _decode_bool(payload)) is None:
            return False
        state.legacy_errors = _toggle_named_value(
            state.legacy_errors, ERROR_TOPICS[topic], value
        )
    elif topic in ALERT_TOPICS:
        if (value := _decode_bool(payload)) is None:
            return False
        state.alerts = _toggle_named_value(state.alerts, ALERT_TOPICS[topic], value)
    else:
        return False
    return True
