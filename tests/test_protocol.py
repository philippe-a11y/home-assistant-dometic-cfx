"""Unit tests for the pure-Python CFX2/CFX5 DDM2 codec."""

import importlib.util
from pathlib import Path
import struct
import sys
import unittest

PROTOCOL_PATH = (
    Path(__file__).parents[1] / "custom_components" / "dometic_cfx5" / "protocol.py"
)
SPEC = importlib.util.spec_from_file_location("dometic_cfx5_protocol", PROTOCOL_PATH)
assert SPEC is not None and SPEC.loader is not None
protocol = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = protocol
SPEC.loader.exec_module(protocol)


def publish(topic: bytes, payload: bytes) -> bytes:
    """Build a test publish frame."""

    return bytes((protocol.Action.PUBLISH,)) + topic + payload


class ProtocolTest(unittest.TestCase):
    """Verify frames against the Mobile Cooling DDM2 representation."""

    def test_subscribe_frame(self) -> None:
        self.assertEqual(
            protocol.subscribe_frame(protocol.TOPIC_PRODUCT_TYPE),
            bytes.fromhex("12 01 00 00 1a"),
        )

    def test_product_and_compartment_autodetection(self) -> None:
        state = protocol.CFX5State()
        state.family = protocol.DeviceFamily.CFX5
        self.assertTrue(
            protocol.parse_publish(
                publish(protocol.TOPIC_PRODUCT_TYPE, struct.pack("<i", 3)), state
            )
        )
        self.assertEqual(state.product_type, 3)
        self.assertEqual(state.compartment_count, 2)
        self.assertEqual(state.model_name, "CFX5 Dual Zone")

    def test_firmware_id_detects_ddm2_generation(self) -> None:
        state = protocol.CFXState()
        self.assertTrue(
            protocol.parse_publish(
                publish(protocol.TOPIC_FIRMWARE_ID, b"MC2\x00"), state
            )
        )
        self.assertEqual(state.family, protocol.DeviceFamily.CFX2)
        self.assertEqual(len(protocol.SUBSCRIPTIONS), 16)

    def test_compartment_arrays(self) -> None:
        state = protocol.CFX5State()
        protocol.parse_publish(
            publish(
                protocol.TOPIC_MEASURED_TEMPERATURE,
                struct.pack("<ii", -18500, 4250),
            ),
            state,
        )
        protocol.parse_publish(
            publish(protocol.TOPIC_COMPARTMENT_POWER, struct.pack("<ii", 1, 0)),
            state,
        )
        self.assertEqual(state.measured_temperature, [-18.5, 4.25])
        self.assertEqual(state.compartment_power, [True, False])

    def test_temperature_range_pairs(self) -> None:
        state = protocol.CFX5State()
        protocol.parse_publish(
            publish(
                protocol.TOPIC_TEMPERATURE_RANGE,
                struct.pack("<iiii", -22000, 10000, -18000, 10000),
            ),
            state,
        )
        self.assertEqual(state.temperature_min, [-22.0, -18.0])
        self.assertEqual(state.temperature_max, [10.0, 10.0])

    def test_set_payload_is_int32_little_endian(self) -> None:
        frame = protocol.set_frame(
            protocol.TOPIC_COOLER_POWER, protocol.encode_int32(True)
        )
        self.assertEqual(frame, bytes.fromhex("11 0b 00 00 1a 01 00 00 00"))

    def test_error_array(self) -> None:
        state = protocol.CFX5State()
        protocol.parse_publish(
            publish(protocol.TOPIC_ERRORS, struct.pack("<HHH", 0, 513, 23)),
            state,
        )
        self.assertEqual(state.errors, (513, 23))
        self.assertIn("Compressor failed to start", state.error_text)
        self.assertIn("Door open too long", state.error_text)

    def test_non_publish_is_ignored(self) -> None:
        state = protocol.CFX5State()
        self.assertFalse(protocol.parse_publish(bytes.fromhex("04"), state))


if __name__ == "__main__":
    unittest.main()
