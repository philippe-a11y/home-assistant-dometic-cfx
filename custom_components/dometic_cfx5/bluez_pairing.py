# ruff: noqa: F821
"""BlueZ Just Works pairing for local Dometic CFX devices."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any

from bleak.backends.bluezdbus.utils import get_dbus_authenticator
from bleak.exc import BleakError
from dbus_fast.aio import MessageBus
from dbus_fast.constants import BusType, MessageType
from dbus_fast.errors import DBusError
from dbus_fast.message import Message
from dbus_fast.service import ServiceInterface, method
from dbus_fast.signature import Variant

_LOGGER = logging.getLogger(__name__)

BLUEZ_SERVICE = "org.bluez"
BLUEZ_ROOT = "/org/bluez"
AGENT_MANAGER_INTERFACE = "org.bluez.AgentManager1"
ADAPTER_INTERFACE = "org.bluez.Adapter1"
DEVICE_INTERFACE = "org.bluez.Device1"
OBJECT_MANAGER_INTERFACE = "org.freedesktop.DBus.ObjectManager"
AGENT_PATH = "/com/dometic/cfx/agent"
AGENT_INTERFACE = "org.bluez.Agent1"
BLUEZ_REJECTED = "org.bluez.Error.Rejected"
PROPERTIES_INTERFACE = "org.freedesktop.DBus.Properties"
STALE_LINK_TIMEOUT_SECONDS = 5.0
BONDED_PREFLIGHT_TIMEOUT_SECONDS = 45.0
FRESH_PAIR_TIMEOUT_SECONDS = 30.0

_AUTH_FAILURE_MARKERS = (
    "org.bluez.error.authenticationfailed",
    "org.bluez.error.authenticationcanceled",
    "org.bluez.error.authenticationrejected",
    "org.bluez.error.authenticationtimeout",
    "authentication failed",
    "insufficient authentication",
    "insufficient encryption",
    "pairing rejected",
)


def is_cfx_bond_mismatch_error(err: BaseException) -> bool:
    """Return whether an error indicates a stale bond the CFX has forgotten.

    A factory reset deletes the long-term key on the cooler while BlueZ still
    holds the old bond, so every encrypted reconnect fails authentication
    until that local bond is removed and Just Works pairing runs again.
    """

    text = str(err).lower()
    return any(marker in text for marker in _AUTH_FAILURE_MARKERS)


class _CFXJustWorksAgent(ServiceInterface):
    """Accept non-interactive Just Works requests for one CFX device."""

    def __init__(self, device_path: str) -> None:
        super().__init__(AGENT_INTERFACE)
        self._device_path = device_path

    def _check_device(self, device: str) -> None:
        if device != self._device_path:
            raise DBusError(BLUEZ_REJECTED, "Pairing request is not for this CFX")

    @method()
    def Release(self) -> None:
        """Handle BlueZ releasing the agent."""

        _LOGGER.debug("BlueZ released the temporary CFX pairing agent")

    @method()
    def RequestPinCode(self, device: "o") -> "s":
        """Reject legacy PIN pairing; the CFX uses LE Just Works."""

        self._check_device(device)
        raise DBusError(BLUEZ_REJECTED, "The CFX does not use a PIN")

    @method()
    def DisplayPinCode(self, device: "o", pincode: "s") -> None:
        """Acknowledge an informational PIN display callback."""

        self._check_device(device)

    @method()
    def RequestPasskey(self, device: "o") -> "u":
        """Reject passkey entry; the CFX uses LE Just Works."""

        self._check_device(device)
        raise DBusError(BLUEZ_REJECTED, "The CFX does not use a passkey")

    @method()
    def DisplayPasskey(self, device: "o", passkey: "u", entered: "q") -> None:
        """Acknowledge an informational passkey display callback."""

        self._check_device(device)

    @method()
    def RequestConfirmation(self, device: "o", passkey: "u") -> None:
        """Confirm the CFX Just Works request."""

        self._check_device(device)
        _LOGGER.debug(
            "Accepted CFX Just Works confirmation for %s (passkey=%06d)",
            device,
            passkey,
        )

    @method()
    def RequestAuthorization(self, device: "o") -> None:
        """Authorize the CFX pairing request."""

        self._check_device(device)
        _LOGGER.debug("Authorized CFX Just Works pairing for %s", device)

    @method()
    def AuthorizeService(self, device: "o", uuid: "s") -> None:
        """Authorize GATT access for the paired CFX."""

        self._check_device(device)
        _LOGGER.debug("Authorized CFX service %s", uuid)

    @method()
    def Cancel(self) -> None:
        """Log cancellation of the current agent request."""

        _LOGGER.debug("BlueZ canceled the temporary CFX pairing request")


def _reply_error(reply: Any, action: str) -> BleakError:
    """Convert a D-Bus error reply into a useful Bleak error."""

    detail = reply.body[0] if reply.body else "unknown error"
    return BleakError(f"{action}: [{reply.error_name}] {detail}")


async def _managed_objects(bus: MessageBus) -> dict[str, Any]:
    """Return BlueZ managed objects."""

    reply = await bus.call(
        Message(
            destination=BLUEZ_SERVICE,
            path="/",
            interface=OBJECT_MANAGER_INTERFACE,
            member="GetManagedObjects",
        )
    )
    if reply.message_type == MessageType.ERROR:
        raise _reply_error(reply, "Unable to inspect BlueZ devices")
    return reply.body[0]


def _find_device(
    objects: dict[str, Any], address: str
) -> tuple[str, dict[str, Any]] | None:
    """Find a local BlueZ Device1 object by Bluetooth address."""

    wanted = address.upper()
    for path, interfaces in objects.items():
        properties = interfaces.get(DEVICE_INTERFACE)
        if not properties:
            continue
        value = properties.get("Address")
        if value is not None and str(value.value).upper() == wanted:
            return path, properties
    return None


async def _set_trusted(bus: MessageBus, device_path: str) -> None:
    """Mark the bond trusted so reconnects need no authorization agent."""

    reply = await bus.call(
        Message(
            destination=BLUEZ_SERVICE,
            path=device_path,
            interface=PROPERTIES_INTERFACE,
            member="Set",
            signature="ssv",
            body=[DEVICE_INTERFACE, "Trusted", Variant("b", True)],
        )
    )
    if reply.message_type == MessageType.ERROR:
        raise _reply_error(reply, "Unable to trust the CFX bond")


async def _disconnect_stale_link(
    bus: MessageBus,
    address: str,
    device_path: str,
    properties: dict[str, Any],
) -> None:
    """Release a BLE link left connected by a previous HA process."""

    connected = properties.get("Connected")
    if connected is None or not bool(connected.value):
        return

    _LOGGER.debug("Disconnecting stale BlueZ CFX link for %s", address)
    reply = await bus.call(
        Message(
            destination=BLUEZ_SERVICE,
            path=device_path,
            interface=DEVICE_INTERFACE,
            member="Disconnect",
        )
    )
    if reply.message_type == MessageType.ERROR and reply.error_name not in {
        "org.bluez.Error.NotConnected",
        "org.bluez.Error.DoesNotExist",
    }:
        raise _reply_error(reply, "Unable to release stale CFX connection")

    try:
        async with asyncio.timeout(STALE_LINK_TIMEOUT_SECONDS):
            while True:
                current = _find_device(await _managed_objects(bus), address)
                if current is None:
                    return
                current_connected = current[1].get("Connected")
                if current_connected is None or not bool(current_connected.value):
                    _LOGGER.debug("Stale BlueZ CFX link for %s released", address)
                    return
                await asyncio.sleep(0.1)
    except TimeoutError as err:
        raise BleakError(
            f"Stale BlueZ connection to CFX {address} did not disconnect"
        ) from err


async def _pair_bonded_cfx_before_bleak(
    bus: MessageBus, address: str, device_path: str
) -> None:
    """Ask BlueZ to restore security and GATT before Bleak calls Connect.

    BlueZ's Connect D-Bus method remains in progress while it waits for GATT
    services, which prevents the app-style Pair call after the physical link.
    Device1.Pair itself performs connect, authentication, and primary-service
    discovery, so it is the only supported ordering equivalent on BlueZ.
    """

    _LOGGER.debug("Starting bonded CFX security/GATT preflight for %s", address)
    try:
        async with asyncio.timeout(BONDED_PREFLIGHT_TIMEOUT_SECONDS):
            reply = await bus.call(
                Message(
                    destination=BLUEZ_SERVICE,
                    path=device_path,
                    interface=DEVICE_INTERFACE,
                    member="Pair",
                )
            )
    except TimeoutError as err:
        raise BleakError(
            f"Bonded CFX security/GATT preflight timed out for {address}"
        ) from err

    if reply.message_type != MessageType.ERROR:
        _LOGGER.info("Bonded CFX security/GATT preflight completed for %s", address)
        return
    if reply.error_name == "org.bluez.Error.AlreadyExists":
        _LOGGER.debug(
            "BlueZ reports the CFX bond already exists; continuing with its "
            "stored keys"
        )
        return
    raise _reply_error(reply, "Bonded CFX security/GATT preflight failed")


async def async_remove_cfx_bluez_bond(address: str) -> bool:
    """Remove one local CFX device and its bond from BlueZ.

    This is intentionally called only when the user removes the Home Assistant
    config entry. It provides a controlled recovery path for a stale bond whose
    physical BLE link still connects but whose GATT services never resolve.
    """

    bus = MessageBus(
        bus_type=BusType.SYSTEM,
        negotiate_unix_fd=True,
        auth=get_dbus_authenticator(),
    )
    try:
        await bus.connect()
        found = _find_device(await _managed_objects(bus), address)
        if found is None:
            _LOGGER.debug(
                "No local BlueZ CFX object for %s; no bond to remove", address
            )
            return False

        device_path, _properties = found
        adapter_path = device_path.rsplit("/", 1)[0]
        reply = await bus.call(
            Message(
                destination=BLUEZ_SERVICE,
                path=adapter_path,
                interface=ADAPTER_INTERFACE,
                member="RemoveDevice",
                signature="o",
                body=[device_path],
            )
        )
        if reply.message_type == MessageType.ERROR:
            if reply.error_name == "org.bluez.Error.DoesNotExist":
                return False
            raise _reply_error(reply, "Unable to remove the CFX bond")

        _LOGGER.info("Removed local BlueZ bond for CFX %s", address)
        return True
    finally:
        with suppress(Exception):
            bus.disconnect()


@asynccontextmanager
async def async_prepare_cfx_bluez(address: str) -> AsyncIterator[bool]:
    """Prepare local BlueZ and keep the CFX authorization agent alive.

    Yield False when the address is not managed by local BlueZ, allowing the
    caller to delegate pairing to a Home Assistant Bluetooth proxy. For local
    BlueZ, the agent remains registered until GATT initialization completes.
    """

    bus = MessageBus(
        bus_type=BusType.SYSTEM,
        negotiate_unix_fd=True,
        auth=get_dbus_authenticator(),
    )
    registered = False
    made_default = False
    try:
        await bus.connect()
        found = _find_device(await _managed_objects(bus), address)
        if found is None:
            _LOGGER.debug(
                "CFX %s has no local BlueZ object; delegating pairing", address
            )
            yield False
            return

        device_path, properties = found
        agent = _CFXJustWorksAgent(device_path)
        bus.export(AGENT_PATH, agent)
        reply = await bus.call(
            Message(
                destination=BLUEZ_SERVICE,
                path=BLUEZ_ROOT,
                interface=AGENT_MANAGER_INTERFACE,
                member="RegisterAgent",
                signature="os",
                body=[AGENT_PATH, "NoInputNoOutput"],
            )
        )
        if reply.message_type == MessageType.ERROR:
            raise _reply_error(reply, "Unable to register CFX pairing agent")
        registered = True

        # Bleak opens a second D-Bus connection for GATT. Making this tightly
        # scoped agent the temporary default lets BlueZ route any encryption or
        # authorization callback raised during service discovery back here.
        reply = await bus.call(
            Message(
                destination=BLUEZ_SERVICE,
                path=BLUEZ_ROOT,
                interface=AGENT_MANAGER_INTERFACE,
                member="RequestDefaultAgent",
                signature="o",
                body=[AGENT_PATH],
            )
        )
        if reply.message_type == MessageType.ERROR:
            _LOGGER.warning(
                "Could not make the temporary CFX pairing agent the BlueZ "
                "default: %s %s",
                reply.error_name,
                reply.body,
            )
        else:
            made_default = True
            _LOGGER.debug(
                "Temporary CFX NoInputNoOutput agent active during GATT setup"
            )

        paired = properties.get("Paired")
        if paired is not None and bool(paired.value):
            trusted = properties.get("Trusted")
            if trusted is None or not bool(trusted.value):
                await _set_trusted(bus, device_path)
                _LOGGER.info("Existing CFX bond for %s marked trusted", address)
            else:
                _LOGGER.debug("CFX %s is already bonded and trusted", address)
            await _disconnect_stale_link(bus, address, device_path, properties)
            await _pair_bonded_cfx_before_bleak(bus, address, device_path)
        else:
            _LOGGER.debug(
                "Starting application-owned CFX Just Works pairing for %s", address
            )
            try:
                async with asyncio.timeout(FRESH_PAIR_TIMEOUT_SECONDS):
                    reply = await bus.call(
                        Message(
                            destination=BLUEZ_SERVICE,
                            path=device_path,
                            interface=DEVICE_INTERFACE,
                            member="Pair",
                        )
                    )
            except TimeoutError as err:
                raise BleakError(
                    f"CFX Just Works pairing timed out for {address}"
                ) from err
            if reply.message_type == MessageType.ERROR:
                raise _reply_error(reply, "CFX Just Works pairing failed")
            await _set_trusted(bus, device_path)
            _LOGGER.info("CFX %s bonded successfully with BlueZ", address)

        yield True
    finally:
        if registered:
            with suppress(Exception):
                await bus.call(
                    Message(
                        destination=BLUEZ_SERVICE,
                        path=BLUEZ_ROOT,
                        interface=AGENT_MANAGER_INTERFACE,
                        member="UnregisterAgent",
                        signature="o",
                        body=[AGENT_PATH],
                    )
                )
            if made_default:
                _LOGGER.debug("Temporary CFX agent removed after GATT initialization")
        with suppress(Exception):
            bus.unexport(AGENT_PATH)
        with suppress(Exception):
            bus.disconnect()
