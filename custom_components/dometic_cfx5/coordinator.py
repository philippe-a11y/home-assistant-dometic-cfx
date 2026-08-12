"""Bluetooth connection and state coordinator for Dometic CFX coolers."""

import asyncio
from datetime import timedelta
import logging
from typing import Any, Literal

from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DDM1_NOTIFY_UUID,
    DDM1_SERVICE_UUID,
    DDM1_WRITE_UUID,
    DDM2_NOTIFY_UUID,
    DDM2_SERVICE_UUID,
    DDM2_WRITE_UUID,
    DEFAULT_NAME,
    INITIAL_DATA_TIMEOUT_SECONDS,
    UPDATE_INTERVAL_SECONDS,
)
from .protocol import (
    SUBSCRIPTIONS as DDM2_SUBSCRIPTIONS,
    TOPIC_BATTERY_PROTECTION as DDM2_TOPIC_BATTERY_PROTECTION,
    TOPIC_COMPARTMENT_POWER as DDM2_TOPIC_COMPARTMENT_POWER,
    TOPIC_COOLER_POWER as DDM2_TOPIC_COOLER_POWER,
    TOPIC_ICEMAKER_POWER as DDM2_TOPIC_ICEMAKER_POWER,
    TOPIC_SET_TEMPERATURE as DDM2_TOPIC_SET_TEMPERATURE,
    Action as DDM2Action,
    CFXState,
    DeviceFamily,
    encode_int32,
    encode_int32_array,
    encode_millivalue_array,
    family_from_name,
    parse_publish as parse_ddm2_publish,
    set_frame as ddm2_set_frame,
    subscribe_frame as ddm2_subscribe_frame,
)
from .protocol_ddm1 import (
    SUBSCRIPTIONS as DDM1_SUBSCRIPTIONS,
    TOPIC_BATTERY_PROTECTION as DDM1_TOPIC_BATTERY_PROTECTION,
    TOPIC_COMPARTMENT_POWER as DDM1_TOPIC_COMPARTMENT_POWER,
    TOPIC_COOLER_POWER as DDM1_TOPIC_COOLER_POWER,
    TOPIC_ICEMAKER_POWER as DDM1_TOPIC_ICEMAKER_POWER,
    TOPIC_SET_TEMPERATURE as DDM1_TOPIC_SET_TEMPERATURE,
    Action as DDM1Action,
    encode_bool as ddm1_encode_bool,
    encode_temperature as ddm1_encode_temperature,
    encode_uint8 as ddm1_encode_uint8,
    parse_publish as parse_ddm1_publish,
    set_frame as ddm1_set_frame,
    subscribe_frame as ddm1_subscribe_frame,
)

_LOGGER = logging.getLogger(__name__)

ProtocolName = Literal["ddm1", "ddm2"]


class DometicCFXCoordinator(DataUpdateCoordinator[CFXState]):
    """Maintain a bonded BLE connection for any app-supported CFX family."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""

        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DEFAULT_NAME,
            update_interval=timedelta(seconds=UPDATE_INTERVAL_SECONDS),
        )
        self.address: str = entry.data[CONF_ADDRESS]
        self.data = CFXState()
        self.connected = False
        self.protocol: ProtocolName | None = None
        self._write_uuid: str | None = None
        self._notify_uuid: str | None = None
        self._client: BleakClientWithServiceCache | None = None
        self._initial_data = asyncio.Event()
        self._ddm1_handshake = asyncio.Event()
        self._ddm1_handshake_stage: Literal["idle", "ping", "hello", "ready"] = "idle"
        self._connect_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self._shutting_down = False

    @callback
    def _mark_disconnected(self, client: BleakClientWithServiceCache) -> None:
        """Handle a Bleak disconnect on the Home Assistant event loop."""

        if client is not self._client:
            return
        self._client = None
        self.connected = False
        if not self._shutting_down:
            self.async_set_update_error(UpdateFailed("Bluetooth disconnected"))

    def _disconnected_callback(self, client: BleakClientWithServiceCache) -> None:
        """Schedule disconnect handling from a Bleak callback."""

        self.hass.loop.call_soon_threadsafe(self._mark_disconnected, client)

    def _has_initial_state(self) -> bool:
        count = self.data.compartment_count
        if self.data.product_type is None or count is None or count < 1:
            return False
        return all(
            len(values) >= count and all(value is not None for value in values[:count])
            for values in (
                self.data.compartment_power,
                self.data.measured_temperature,
                self.data.set_temperature,
            )
        )

    @callback
    def _notification_callback(
        self, _characteristic: BleakGATTCharacteristic, data: bytearray
    ) -> None:
        """Decode a DDM1 or DDM2 notification and publish its state."""

        frame = bytes(data)
        if not frame:
            return

        if self.protocol == "ddm1":
            if frame[0] == DDM1Action.ACK:
                self._handle_ddm1_ack()
                return
            if frame[0] == DDM1Action.NAK:
                _LOGGER.warning("CFX3 returned DDM1 NAK: %s", frame.hex(" "))
                return
            if not parse_ddm1_publish(frame, self.data):
                _LOGGER.debug("Ignoring DDM1 frame: %s", frame.hex(" "))
                return
            self.hass.async_create_task(
                self._async_ack_ddm1_publish(),
                f"Acknowledge Dometic CFX3 publish {self.address}",
            )
        elif self.protocol == "ddm2":
            if frame[0] == DDM2Action.NAK:
                _LOGGER.warning("CFX returned DDM2 NAK: %s", frame.hex(" "))
                return
            if not parse_ddm2_publish(frame, self.data):
                _LOGGER.debug("Ignoring DDM2 frame: %s", frame.hex(" "))
                return
        else:
            return

        if self._has_initial_state():
            self._initial_data.set()
        self.async_set_updated_data(self.data)

    @callback
    def _handle_ddm1_ack(self) -> None:
        """Advance the app's CFX3 PING/HELLO handshake."""

        if self._ddm1_handshake_stage == "ping":
            self._ddm1_handshake_stage = "hello"
            self.hass.async_create_task(
                self._async_write_frame(bytes((DDM1Action.HELLO,))),
                f"Send Dometic CFX3 HELLO {self.address}",
            )
        elif self._ddm1_handshake_stage == "hello":
            self._ddm1_handshake_stage = "ready"
            self._ddm1_handshake.set()

    async def _async_ack_ddm1_publish(self) -> None:
        """Acknowledge one CFX3 publish without leaking task errors."""

        try:
            await self._async_write_frame(bytes((DDM1Action.ACK,)))
        except HomeAssistantError:
            _LOGGER.debug("Acknowledging a CFX3 publish failed", exc_info=True)

    async def _async_update_data(self) -> CFXState:
        """Ensure that the persistent push connection is established."""

        if self._client is None or not self._client.is_connected:
            await self._async_connect()
        return self.data

    def _select_protocol(self, client: BleakClientWithServiceCache) -> None:
        """Select a protocol only after verifying its GATT service."""

        if client.services.get_service(DDM1_SERVICE_UUID) is not None:
            self.protocol = "ddm1"
            self._write_uuid = DDM1_WRITE_UUID
            self._notify_uuid = DDM1_NOTIFY_UUID
            self.data.family = DeviceFamily.CFX3
            return
        if client.services.get_service(DDM2_SERVICE_UUID) is not None:
            self.protocol = "ddm2"
            self._write_uuid = DDM2_WRITE_UUID
            self._notify_uuid = DDM2_NOTIFY_UUID
            return
        raise UpdateFailed("Device exposes neither a CFX DDM1 nor DDM2 service")

    def _selected_notify_uuid(self) -> str:
        """Return the selected notify UUID or fail setup."""

        if self._notify_uuid is None:
            raise UpdateFailed("CFX notify characteristic is not selected")
        return self._notify_uuid

    async def _async_subscribe(self) -> None:
        """Perform the generation-specific handshake and official subscriptions."""

        if self.protocol == "ddm1":
            self._ddm1_handshake.clear()
            self._ddm1_handshake_stage = "ping"
            await self._async_write_frame(bytes((DDM1Action.PING,)))
            async with asyncio.timeout(INITIAL_DATA_TIMEOUT_SECONDS):
                await self._ddm1_handshake.wait()
            for topic in DDM1_SUBSCRIPTIONS:
                await self._async_write_frame(ddm1_subscribe_frame(topic))
                await asyncio.sleep(0)
            return

        if self.protocol == "ddm2":
            for topic in DDM2_SUBSCRIPTIONS:
                await self._async_write_frame(ddm2_subscribe_frame(topic))
            return
        raise UpdateFailed("CFX protocol has not been selected")

    async def _async_connect(self) -> None:
        """Pair, connect, negotiate the protocol and subscribe."""

        async with self._connect_lock:
            if self._client is not None and self._client.is_connected:
                return

            ble_device = bluetooth.async_ble_device_from_address(
                self.hass, self.address, connectable=True
            )
            if ble_device is None:
                raise UpdateFailed(f"CFX {self.address} is not in Bluetooth range")

            detected = family_from_name(ble_device.name)
            if detected is not DeviceFamily.UNKNOWN:
                self.data.family = detected
            self._initial_data.clear()
            client: BleakClientWithServiceCache | None = None
            try:
                client = await establish_connection(
                    BleakClientWithServiceCache,
                    ble_device,
                    DEFAULT_NAME,
                    disconnected_callback=self._disconnected_callback,
                    pair=True,
                )
                self._select_protocol(client)
                self._client = client
                notify_uuid = self._selected_notify_uuid()
                await client.start_notify(notify_uuid, self._notification_callback)
                await self._async_subscribe()
                async with asyncio.timeout(INITIAL_DATA_TIMEOUT_SECONDS):
                    await self._initial_data.wait()
            except TimeoutError as err:
                await self._async_disconnect_failed_client(client)
                raise UpdateFailed(
                    "Connected to the CFX but did not complete its handshake or initial state"
                ) from err
            except (BleakError, UpdateFailed) as err:
                await self._async_disconnect_failed_client(client)
                if isinstance(err, UpdateFailed):
                    raise
                raise UpdateFailed(
                    f"Unable to communicate with the CFX: {err}"
                ) from err

            self.connected = True
            _LOGGER.info(
                "Connected to %s at %s using %s (%s compartment(s))",
                self.data.model_name,
                self.address,
                self.protocol.upper(),
                self.data.compartment_count,
            )

    async def _async_disconnect_failed_client(
        self, client: BleakClientWithServiceCache | None
    ) -> None:
        if client is not None and client.is_connected:
            await client.disconnect()
        self._client = None
        self.connected = False

    async def _async_get_client(self) -> BleakClientWithServiceCache:
        """Return a connected client, reconnecting if needed."""

        if self._client is None or not self._client.is_connected:
            # A write performed by the setup handshake must not recursively wait
            # for the connection lock if the peripheral drops off mid-setup.
            if self._connect_lock.locked():
                raise HomeAssistantError("CFX disconnected during setup")
            await self._async_connect()
        if self._client is None:
            raise HomeAssistantError("CFX is not connected")
        return self._client

    async def _async_write_frame(self, frame: bytes) -> None:
        """Write one frame and wait for its GATT response."""

        async with self._write_lock:
            client = await self._async_get_client()
            if self._write_uuid is None:
                raise HomeAssistantError("CFX write characteristic is not selected")
            try:
                await client.write_gatt_char(self._write_uuid, frame, response=True)
            except BleakError as err:
                if client.is_connected:
                    await client.disconnect()
                raise HomeAssistantError(f"Writing to the CFX failed: {err}") from err

    def _compartment_count(self) -> int:
        count = self.data.compartment_count
        if count is None or count < 1:
            raise HomeAssistantError("The CFX compartment count is not known yet")
        return count

    @staticmethod
    def _require_complete_array(values: list[Any], count: int, label: str) -> None:
        if len(values) < count or any(value is None for value in values[:count]):
            raise HomeAssistantError(
                f"Cannot change {label} before all compartment values are known"
            )

    async def async_set_cooler_power(self, enabled: bool) -> None:
        """Set global cooler power."""

        if self.protocol == "ddm1":
            frame = ddm1_set_frame(DDM1_TOPIC_COOLER_POWER, ddm1_encode_bool(enabled))
        else:
            frame = ddm2_set_frame(DDM2_TOPIC_COOLER_POWER, encode_int32(enabled))
        await self._async_write_frame(frame)
        self.data.cooler_power = enabled
        self.async_set_updated_data(self.data)

    async def async_set_compartment_power(self, index: int, enabled: bool) -> None:
        """Set one compartment using the generation-specific representation."""

        count = self._compartment_count()
        if index < 0 or index >= count:
            raise HomeAssistantError(f"Invalid CFX compartment index {index}")
        if enabled and self.data.cooler_power is False:
            await self.async_set_cooler_power(True)

        if self.protocol == "ddm1":
            await self._async_write_frame(
                ddm1_set_frame(
                    DDM1_TOPIC_COMPARTMENT_POWER[index], ddm1_encode_bool(enabled)
                )
            )
            self.data.compartment_power[index] = enabled
        else:
            self._require_complete_array(self.data.compartment_power, count, "power")
            values = self.data.compartment_power[:count]
            values[index] = enabled
            await self._async_write_frame(
                ddm2_set_frame(DDM2_TOPIC_COMPARTMENT_POWER, encode_int32_array(values))
            )
            self.data.compartment_power = values
        self.async_set_updated_data(self.data)

    async def async_set_temperature(self, index: int, temperature: float) -> None:
        """Set one target temperature."""

        count = self._compartment_count()
        if index < 0 or index >= count:
            raise HomeAssistantError(f"Invalid CFX compartment index {index}")

        if self.protocol == "ddm1":
            await self._async_write_frame(
                ddm1_set_frame(
                    DDM1_TOPIC_SET_TEMPERATURE[index],
                    ddm1_encode_temperature(temperature),
                )
            )
            self.data.set_temperature[index] = temperature
        else:
            self._require_complete_array(
                self.data.set_temperature, count, "target temperature"
            )
            values = self.data.set_temperature[:count]
            values[index] = temperature
            await self._async_write_frame(
                ddm2_set_frame(
                    DDM2_TOPIC_SET_TEMPERATURE,
                    encode_millivalue_array(values),
                )
            )
            self.data.set_temperature = values
        self.async_set_updated_data(self.data)

    async def async_set_battery_protection(self, level: int) -> None:
        """Set the DC battery protection level."""

        if level not in (0, 1, 2):
            raise HomeAssistantError(f"Invalid battery protection level {level}")
        if self.protocol == "ddm1":
            frame = ddm1_set_frame(
                DDM1_TOPIC_BATTERY_PROTECTION, ddm1_encode_uint8(level)
            )
        else:
            frame = ddm2_set_frame(DDM2_TOPIC_BATTERY_PROTECTION, encode_int32(level))
        await self._async_write_frame(frame)
        self.data.battery_protection = level
        self.async_set_updated_data(self.data)

    async def async_set_icemaker_power(self, enabled: bool) -> None:
        """Set ice maker power."""

        if self.protocol == "ddm1":
            frame = ddm1_set_frame(DDM1_TOPIC_ICEMAKER_POWER, ddm1_encode_bool(enabled))
        else:
            frame = ddm2_set_frame(DDM2_TOPIC_ICEMAKER_POWER, encode_int32(enabled))
        await self._async_write_frame(frame)
        self.data.icemaker_power = enabled
        self.async_set_updated_data(self.data)

    async def async_shutdown(self) -> None:
        """Stop notifications and release the BLE connection."""

        self._shutting_down = True
        client = self._client
        notify_uuid = self._notify_uuid
        self._client = None
        self.connected = False
        if client is not None and client.is_connected:
            if notify_uuid is not None:
                try:
                    await client.stop_notify(notify_uuid)
                except BleakError:
                    _LOGGER.debug("Stopping CFX notifications failed", exc_info=True)
            await client.disconnect()
        await super().async_shutdown()


# Compatibility alias for config entries created by the CFX5-only preview.
DometicCFX5Coordinator = DometicCFXCoordinator
