"""Unit tests for the CFX3 DDM1 codec."""

import importlib.util
from pathlib import Path
import struct
import sys
import types
import unittest

COMPONENT_PATH = Path(__file__).parents[1] / "custom_components" / "dometic_cfx5"
PACKAGE_NAME = "test_dometic_cfx"
package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(COMPONENT_PATH)]
sys.modules[PACKAGE_NAME] = package


def _load_module(name: str, filename: str) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(
        f"{PACKAGE_NAME}.{name}", COMPONENT_PATH / filename
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


protocol = _load_module("protocol", "protocol.py")
ddm1 = _load_module("protocol_ddm1", "protocol_ddm1.py")


def publish(topic: bytes, payload: bytes) -> bytes:
    """Build one test DDM1 publish."""

    return bytes((ddm1.Action.PUBLISH,)) + topic + payload


class DDM1ProtocolTest(unittest.TestCase):
    """Verify CFX3 frames extracted from Mobile Cooling."""

    def test_handshake_and_subscribe_actions(self) -> None:
        self.assertEqual(bytes((ddm1.Action.PING,)), b"\x02")
        self.assertEqual(bytes((ddm1.Action.HELLO,)), b"\x03")
        self.assertEqual(bytes((ddm1.Action.ACK,)), b"\x04")
        self.assertEqual(
            ddm1.subscribe_frame(ddm1.TOPIC_PRODUCT_TYPE),
            bytes.fromhex("01 00 c4 00 00"),
        )
        self.assertEqual(len(ddm1.SUBSCRIPTIONS), 34)

    def test_product_type_and_individual_compartments(self) -> None:
        state = protocol.CFXState()
        self.assertTrue(
            ddm1.parse_publish(publish(ddm1.TOPIC_PRODUCT_TYPE, b"\x03"), state)
        )
        ddm1.parse_publish(
            publish(ddm1.TOPIC_MEASURED_TEMPERATURE[1], struct.pack("<h", 425)),
            state,
        )
        ddm1.parse_publish(publish(ddm1.TOPIC_COMPARTMENT_POWER[1], b"\x01"), state)
        self.assertEqual(state.family, protocol.DeviceFamily.CFX3)
        self.assertEqual(state.compartment_count, 2)
        self.assertEqual(state.measured_temperature, [None, 42.5])
        self.assertEqual(state.compartment_power, [None, True])

    def test_temperature_write_is_signed_deci_degree(self) -> None:
        frame = ddm1.set_frame(
            ddm1.TOPIC_SET_TEMPERATURE[0], ddm1.encode_temperature(-18.5)
        )
        self.assertEqual(frame, bytes.fromhex("00 00 02 01 01 47 ff"))

    def test_voltage_and_error_toggling(self) -> None:
        state = protocol.CFXState()
        ddm1.parse_publish(publish(ddm1.TOPIC_VOLTAGE, struct.pack("<H", 123)), state)
        error_topic = bytes.fromhex("00 03 04 01")
        ddm1.parse_publish(publish(error_topic, b"\x01"), state)
        self.assertEqual(state.voltage, 12.3)
        self.assertTrue(state.has_problem)
        self.assertIn("Communication fault", state.error_text)
        ddm1.parse_publish(publish(error_topic, b"\x00"), state)
        self.assertFalse(state.has_problem)


if __name__ == "__main__":
    unittest.main()
