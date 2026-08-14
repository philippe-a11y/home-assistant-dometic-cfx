# ruff: noqa: F821
"""BlueZ Just Works pairing for local Dometic CFX devices."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any

from bleak.backends.bluezdbus.utils import get_dbus_authenticator
from bleak.exc import BleakError

from .const import DDM1_NOTIFY_UUID, DDM2_NOTIFY_UUID
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
    # Genuine bond-defect signatures only. AuthenticationCanceled and
    # AuthenticationRejected are deliberately NOT listed: the CFX returns
    # those when it is simply not in Bluetooth pairing mode, and treating
    # them as a stale bond would destroy a perfectly good local bond on
    # every transient refusal.
    "org.bluez.error.authenticationfailed",
    "authentication failed",
    "insufficient authentication",
    "insufficient encryption",
)


# --- Kernel mgmt socket: central-initiated link encryption ------------------
#
# The HCI snoop of the Mobile Cooling app shows that on bonded reconnects the
# central proactively issues LE Start Encryption ~270ms after the connection
# comes up; the CFX neither sends a Security Request nor returns ATT security
# errors, so neither BlueZ nor the kernel would ever encrypt on their own.
# The mgmt Pair Device command on an already-bonded, connected device does not
# re-pair: the kernel elevates the connection security, which triggers
# LE Start Encryption with the stored long-term key - the Linux equivalent of
# ESPHome's esp_ble_set_encryption().

_AF_BLUETOOTH = 31
_BTPROTO_HCI = 1
_HCI_DEV_NONE = 0xFFFF
_HCI_CHANNEL_CONTROL = 3
_MGMT_OP_PAIR_DEVICE = 0x0019
_MGMT_OP_CANCEL_PAIR_DEVICE = 0x001A
_MGMT_EV_CMD_COMPLETE = 0x0001
_MGMT_EV_CMD_STATUS = 0x0002


def _mgmt_pair_device_blocking(
    adapter_index: int, address: str, addr_type: int, timeout: float
) -> int:
    """Elevate link security via the kernel mgmt interface (blocking).

    Returns the mgmt status code (0 = success). Raises OSError when the
    mgmt socket is unavailable.
    """

    import ctypes
    import select
    import socket as pysocket
    import struct as pystruct
    import time

    sock = pysocket.socket(
        _AF_BLUETOOTH, pysocket.SOCK_RAW | pysocket.SOCK_CLOEXEC, _BTPROTO_HCI
    )
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        sockaddr = pystruct.pack(
            "<HHH", _AF_BLUETOOTH, _HCI_DEV_NONE, _HCI_CHANNEL_CONTROL
        )
        buf = ctypes.create_string_buffer(sockaddr)
        if libc.bind(sock.fileno(), buf, len(sockaddr)) != 0:
            errno_ = ctypes.get_errno()
            raise OSError(errno_, "mgmt bind failed")

        bdaddr = bytes(reversed(bytes.fromhex(address.replace(":", ""))))
        params = bdaddr + bytes((addr_type, 0x03))  # io cap NoInputNoOutput
        cmd = (
            pystruct.pack("<HHH", _MGMT_OP_PAIR_DEVICE, adapter_index, len(params))
            + params
        )
        _LOGGER.debug(
            "mgmt: sending Pair Device on hci%d addr_type=%d", adapter_index,
            addr_type,
        )
        sock.send(cmd)

        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                cancel = (
                    pystruct.pack(
                        "<HHH", _MGMT_OP_CANCEL_PAIR_DEVICE, adapter_index, 7
                    )
                    + bdaddr
                    + bytes((addr_type,))
                )
                sock.send(cancel)
                raise TimeoutError("mgmt Pair Device timed out")
            readable, _, _ = select.select([sock], [], [], remaining)
            if not readable:
                continue
            event = sock.recv(512)
            if len(event) < 6:
                continue
            ev_code, ev_index, ev_len = pystruct.unpack("<HHH", event[:6])
            payload = event[6 : 6 + ev_len]
            if ev_index != adapter_index or len(payload) < 3:
                continue
            (ev_opcode,) = pystruct.unpack("<H", payload[:2])
            _LOGGER.debug(
                "mgmt: event code=%#06x opcode=%#06x status=%#04x",
                ev_code, ev_opcode,
                payload[2] if len(payload) > 2 else 0xFF,
            )
            if ev_opcode != _MGMT_OP_PAIR_DEVICE:
                continue
            if ev_code == _MGMT_EV_CMD_STATUS:
                return payload[2]
            if ev_code == _MGMT_EV_CMD_COMPLETE:
                return payload[2]
    finally:
        sock.close()


async def _mgmt_force_encryption(
    device_path: str, address: str, address_type: str
) -> None:
    """Trigger LE Start Encryption with the stored keys via mgmt."""

    try:
        adapter_index = int(device_path.split("/hci", 1)[1].split("/", 1)[0])
    except (IndexError, ValueError):
        adapter_index = 0
    addr_type = 2 if address_type == "random" else 1
    try:
        status = await asyncio.to_thread(
            _mgmt_pair_device_blocking, adapter_index, address, addr_type, 10.0
        )
    except OSError as err:
        raise BleakError(
            f"CFX {address} mgmt encryption unavailable: [Errno {err.errno}] "
            f"{err.strerror or err}"
        ) from err
    except TimeoutError as err:
        raise BleakError(
            f"CFX {address} mgmt encryption timed out"
        ) from err
    _LOGGER.debug("mgmt: Pair Device returned status %#04x", status)
    if status != 0:
        raise BleakError(
            f"CFX {address} link encryption via mgmt failed with status "
            f"{status:#04x}"
            + (
                " (authentication failed - the cooler rejected the stored key)"
                if status in (0x05, 0x0E)
                else ""
            )
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


async def _find_notify_char(
    bus: MessageBus, device_path: str
) -> str | None:
    """Return the D-Bus path of the DDM notify characteristic, if exported."""

    objects = await _managed_objects(bus)
    for path, interfaces in objects.items():
        if not path.startswith(device_path):
            continue
        char = interfaces.get("org.bluez.GattCharacteristic1")
        if char is None:
            continue
        uuid = char.get("UUID")
        if uuid is not None and str(uuid.value).lower() in (
            DDM1_NOTIFY_UUID,
            DDM2_NOTIFY_UUID,
        ):
            return path
    return None


async def _force_encrypted_link(
    bus: MessageBus, address: str, device_path: str
) -> None:
    """Connect via BlueZ and wait for the encrypted, service-resolved link.

    The Mobile Cooling HCI snoop shows the CFX is simply a difficult peer:
    the app itself retries the LE connection many times before one attempt
    survives long enough to encrypt (the central sends LE Start Encryption
    ~270ms after connecting, which BlueZ does automatically for a bonded
    device). There is no user-space "encrypt now" call - mgmt Pair Device
    is refused with Already Paired on a bonded device. So the robust
    approach is the app's approach: ask BlueZ to Connect, and on the
    transient le-connection-abort-by-local just let the caller retry
    quickly. Once BlueZ reports ServicesResolved the link is encrypted and
    Bleak can attach.
    """

    _LOGGER.debug("Connecting CFX %s via BlueZ (bonded, encrypted)", address)
    try:
        async with asyncio.timeout(BONDED_PREFLIGHT_TIMEOUT_SECONDS):
            reply = await bus.call(
                Message(
                    destination=BLUEZ_SERVICE,
                    path=device_path,
                    interface=DEVICE_INTERFACE,
                    member="Connect",
                )
            )
    except TimeoutError as err:
        raise BleakError(f"Bonded CFX connect timed out for {address}") from err

    if reply.message_type == MessageType.ERROR:
        if reply.error_name not in ("org.bluez.Error.AlreadyConnected",):
            body_text = " ".join(str(part) for part in reply.body).lower()
            if "abort" in body_text or "timeout" in body_text:
                # Transient - the cooler is a flaky peer, retry quickly.
                raise BleakError(
                    f"CFX {address} link dropped during connect "
                    f"({reply.error_name}); will retry"
                )
            raise _reply_error(reply, "Bonded CFX connect failed")

    # Connect returned success (or already connected): BlueZ only completes
    # Connect for a bonded device after the link is encrypted and services
    # are resolved. Confirm ServicesResolved for good measure.
    for _ in range(20):
        reply = await bus.call(
            Message(
                destination=BLUEZ_SERVICE,
                path=device_path,
                interface="org.freedesktop.DBus.Properties",
                member="Get",
                signature="ss",
                body=[DEVICE_INTERFACE, "ServicesResolved"],
            )
        )
        if (
            reply.message_type != MessageType.ERROR
            and reply.body
            and bool(reply.body[0].value)
        ):
            _LOGGER.info(
                "CFX %s connected with an encrypted, service-resolved link",
                address,
            )
            return
        await asyncio.sleep(0.25)
    _LOGGER.debug(
        "CFX %s connected but services not resolved yet; letting Bleak proceed",
        address,
    )

async def _pair_bonded_cfx_before_bleak(
    bus: MessageBus, address: str, device_path: str
) -> None:
    """Ask BlueZ to restore security and GATT before Bleak calls Connect.

    BlueZ's Connect D-Bus method remains in progress while it waits for GATT
    services, which prevents the app-style Pair call after the physical link.
    Device1.Pair itself performs connect, authentication, and primary-service
    discovery, so it is the only supported ordering equivalent on BlueZ.
    For a device BlueZ already considers paired, Pair returns AlreadyExists
    without touching the link at all, so encryption must then be established
    explicitly by _force_encrypted_link.
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
            "BlueZ reports the CFX bond already exists; establishing an "
            "encrypted link with its stored keys"
        )
        await _force_encrypted_link(bus, address, device_path)
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
            # Right after a Home Assistant restart the local adapter often
            # has not seen the cooler's advertisement yet, so a missing
            # device object does not mean a proxy is responsible. Give
            # local discovery a moment before delegating.
            deadline = asyncio.get_event_loop().time() + 10.0
            while found is None and asyncio.get_event_loop().time() < deadline:
                await asyncio.sleep(0.5)
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
            # Do NOT run a custom connect/encryption preflight here. Bleak's
            # own connect() already does the right thing for a bonded device:
            # it calls Connect (BlueZ then encrypts with the stored key
            # automatically) and, crucially, handles the expected
            # le-connection-abort-by-local by waiting for the disconnect
            # signal and retrying in a loop on the same D-Bus connection. A
            # separate preflight on its own D-Bus connection only races and
            # sabotages that recovery, which is why bonded reconnects failed.
            _LOGGER.debug(
                "CFX %s is bonded; letting bleak establish the encrypted link",
                address,
            )
            yield True
            return
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
                pair_error = _reply_error(reply, "CFX Just Works pairing failed")
                if "authenticationcanceled" in str(pair_error).lower():
                    _LOGGER.warning(
                        "CFX %s refused the pairing attempt. Put the cooler "
                        "into Bluetooth pairing mode (hold its Bluetooth "
                        "button until the symbol blinks) and reload the "
                        "integration or press the re-pair button",
                        address,
                    )
                raise pair_error
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
