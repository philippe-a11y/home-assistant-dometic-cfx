"""Config flow for Dometic CFX Bluetooth discovery."""

from typing import Any

import voluptuous as vol

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_ADDRESS

from .const import DEFAULT_NAME, DOMAIN, SERVICE_UUIDS


def _matches_cfx5(discovery: BluetoothServiceInfoBleak) -> bool:
    """Return whether an advertisement could be a CFX5."""

    service_uuids = {uuid.lower() for uuid in discovery.service_uuids}
    name = discovery.name or ""
    return bool(SERVICE_UUIDS & service_uuids) or name.upper().startswith(
        ("CFX", "MC1", "MC2", "MC3")
    )


def _discovery_title(discovery: BluetoothServiceInfoBleak) -> str:
    """Return a stable, readable discovery title without extra dependencies."""

    if discovery.name and discovery.name != discovery.address:
        return discovery.name
    return f"{DEFAULT_NAME} {discovery.address[-5:]}"


class DometicCFXConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle setup for all app-supported CFX generations."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize discovery state."""

        self._discovery_info: BluetoothServiceInfoBleak | None = None
        self._discovered_devices: dict[str, BluetoothServiceInfoBleak] = {}

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle automatic Bluetooth discovery."""

        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        self._discovery_info = discovery_info
        self.context["title_placeholders"] = {"name": _discovery_title(discovery_info)}
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm an automatically discovered cooler."""

        if self._discovery_info is None:
            return self.async_abort(reason="no_devices_found")

        if user_input is not None:
            discovery = self._discovery_info
            return self.async_create_entry(
                title=_discovery_title(discovery),
                data={CONF_ADDRESS: discovery.address},
            )

        return self.async_show_form(
            step_id="confirm",
            description_placeholders={
                "name": self._discovery_info.name or DEFAULT_NAME,
                "address": self._discovery_info.address,
            },
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user choose a currently discovered CFX device."""

        if user_input is not None:
            discovery = self._discovered_devices[user_input[CONF_ADDRESS]]
            await self.async_set_unique_id(discovery.address, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=_discovery_title(discovery),
                data={CONF_ADDRESS: discovery.address},
            )

        await bluetooth.async_request_active_scan(self.hass)
        current_ids = self._async_current_ids(include_ignore=False)
        for discovery in async_discovered_service_info(self.hass):
            if (
                discovery.address in current_ids
                or discovery.address in self._discovered_devices
                or not _matches_cfx5(discovery)
            ):
                continue
            self._discovered_devices[discovery.address] = discovery

        if not self._discovered_devices:
            return self.async_abort(reason="no_devices_found")

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): vol.In(
                        {
                            info.address: _discovery_title(info)
                            for info in self._discovered_devices.values()
                        }
                    )
                }
            ),
        )
