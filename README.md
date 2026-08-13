<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="custom_components/dometic_cfx5/brand/dark_logo.png">
    <img src="custom_components/dometic_cfx5/brand/logo.png" alt="Dometic CFX" width="470">
  </picture>
</p>

# Dometic CFX for Home Assistant

Native Home Assistant custom integration for the CFX generations supported by Dometic Mobile Cooling 2.0.32: **CFX2, CFX3 and CFX5**. It discovers a nearby cooler automatically, connects locally over Bluetooth, selects DDM1 or DDM2 from the verified GATT service, detects its product type and compartment count, and creates the matching entities without YAML configuration.

> **Status: hardware validation needed.** Both codecs, protocol selection and the Home Assistant structure are tested without hardware. Each generation still needs a physical connection test before this is production-ready.

## Supported families

| Family | App ID | BLE protocol | Status |
|---|---|---|---|
| CFX2, including dual-zone models | `MC2` / `MC3` | DDM2 | Implemented, hardware test pending |
| CFX3, including 55IM/75DZ/95DZ | `CFX3` | DDM1 | Implemented, hardware test pending |
| CFX5, all product types exposed by the app | `MC1` | DDM2 | Implemented, hardware test pending |

The integration keeps its original internal domain `dometic_cfx5` so installations of the first CFX5-only preview remain compatible.

## Why this implementation is different

The earlier ESPHome proof of concept does control a CFX5, but it can make the cooler report a communication fault. Analysis of Mobile Cooling found four important differences:

| Earlier implementation | Mobile Cooling / this integration |
|---|---|
| Subscribes to about 50 partly unknown topics across several classes | Subscribes only to parameters defined for the CFX5 controller class `0x1A` |
| Sends another write after a timer | Waits for the GATT response to every write |
| Encodes switches as one byte | Encodes DDM2 booleans/enums as 32-bit little-endian integers |
| Maps parameter `0x03` to global cooler power and `0x0B` to compartment power | Uses `0x03` for the compartment-power array and `0x0B` for global cooler power |

These changes remove the most plausible triggers for the communication fault. This still needs confirmation on the physical cooler.

## Features

- Bluetooth discovery from both DDM1 and DDM2 service UUIDs, with Dometic name-prefix fallbacks
- Service-based protocol validation before any CFX command is sent
- CFX-specific BlueZ bonding sequence matching Mobile Cooling: physical link,
  Just Works bond, then GATT service discovery
- CFX3 PING → ACK → HELLO → ACK handshake from the original app
- Automatic distinction between CFX2 (`MC2`/`MC3`) and CFX5 (`MC1`)
- Automatic detection of:
  - single zone
  - single zone with ice maker
  - dual zone
  - compartment count
- One climate entity per detected compartment
- Global cooler power switch
- Ice maker switch when supported
- Battery protection selector
- Compartment temperature and door state
- Voltage and AC/DC source
- Detected product model
- Decoded DDM1 and DDM2 error/alert states and problem sensor
- Persistent connection with automatic reconnect

## Installation

### HACS (recommended)

[![Open your Home Assistant instance and add this repository to HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=philippe-a11y&repository=home-assistant-dometic-cfx&category=integration)

1. Open the button above and add the repository as an **Integration**.
2. Install **Dometic CFX** in HACS.
3. Restart Home Assistant.
4. Open **Settings → Devices & services** and add or accept the discovered **Dometic CFX** integration.

Alternatively, open **HACS → Integrations → ⋮ → Custom repositories**, add
`https://github.com/philippe-a11y/home-assistant-dometic-cfx`, and select **Integration**.

### Manual installation

1. Copy [`custom_components/dometic_cfx5`](custom_components/dometic_cfx5) into your Home Assistant configuration directory so the final path is:

   ```text
   /config/custom_components/dometic_cfx5/
   ```

2. Restart Home Assistant.
3. Turn on the CFX and make sure Bluetooth is enabled. Open its Bluetooth menu
   and start **PAIR** mode for the first connection; the 60-second window must
   still be active when Home Assistant begins setup.
4. Stop the Dometic app and disable the old ESPHome BLE client while testing. A competing connection can prevent Home Assistant from connecting.
5. Open **Settings → Devices & services**. The cooler should appear as a discovered **Dometic CFX** integration. Alternatively select **Add integration → Dometic CFX**.

No MAC address, product type or zone count is entered manually.

### Bluetooth adapter or proxy

The integration needs an adapter that supports active BLE connections. A directly attached Home Assistant Bluetooth adapter is useful for the first test because it removes a proxy as another variable.

## First hardware test

Enable debug logging temporarily:

```yaml
logger:
  logs:
    custom_components.dometic_cfx5: debug
```

Then test in this order:

1. Add the discovered cooler and verify that setup finishes.
2. Confirm product type, compartment count, measured temperature and target temperature.
3. Change the target by 1 °C and wait for the published state to return.
4. Toggle the compartment through its climate entity.
5. Check the cooler display after several minutes for a communication fault.
6. Only then test global power, battery protection and the ice maker if present.

If setup fails, collect the Home Assistant debug log from the first discovery through disconnect. Do not run the Dometic app or the old ESPHome client at the same time.

### Repairing a stale BlueZ bond

If the log reports both `already bonded in BlueZ` and `Physical BLE reconnect`
but never reports `GATT services are ready`, the local BlueZ bond is stale.
Remove the Dometic CFX integration entry in Home Assistant. Starting with
v0.2.17 this also removes only that CFX bond from the Home Assistant Bluetooth
adapter. Then put the cooler's Bluetooth menu into **PAIR** mode and add the
automatically discovered device again.

On bonded reconnects, the integration asks BlueZ to perform its combined
connect, security, and GATT `Pair` operation before Bleak starts a separate
`Connect`. This avoids BlueZ rejecting the app-style post-connect security
request with `org.bluez.Error.InProgress`.

## Protocol notes

The app uses these two CFX transports:

| Protocol | Service | Write | Notify |
|---|---|---|---|
| CFX3 DDM1 | `537a0300…d515` | `537a0301…d515` | `537a0302…d515` |
| CFX2/CFX5 DDM2 | `537a0400…d515` | `537a0401…d515` | `537a0402…d515` |

DDM1 uses publish `00`, subscribe `01`, PING `02`, HELLO `03` and ACK `04`. Its booleans/enums are one byte, temperatures are signed little-endian deci-degrees, and every compartment has its own topic. The integration uses the app's 33 dashboard subscriptions plus battery protection.

DDM2 uses publish `10`, set `11` and subscribe `12`. Its booleans/enums and structure members are 32-bit little-endian values. Compartment values are arrays.

For CFX2/CFX5, the integration sends the app's 13 controller dashboard subscriptions, battery protection, and the app's `cfg.fwid` identity parameter. That identity reports `MC1`, `MC2` or `MC3`; no user-selected model is trusted for protocol selection.

The analyzed XAPK had SHA-256:

```text
6b95f83c1cafc2506c97cfd71fa03d4ab77b752679b8cf9402b5574d9289cc41
```

No application binary or Dometic asset is distributed in this repository.

## Development checks

Run the protocol tests:

```bash
python -m unittest discover -s tests -v
```

The test suite covers both protocol codecs, family and zone detection, encodings, error handling and the official subscription sets. Home Assistant's `hassfest` checks and imports are also run before packaging.

## ESPHome variant

The original ESPHome implementation remains a separate project at
[`philippe-a11y/esphome-dometic-cfx5`](https://github.com/philippe-a11y/esphome-dometic-cfx5).
Do not run that BLE client and this Home Assistant integration against the cooler
at the same time.

## License

MIT
