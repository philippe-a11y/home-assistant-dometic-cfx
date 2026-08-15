<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="custom_components/dometic_cfx5/brand/dark_logo.png">
    <img src="custom_components/dometic_cfx5/brand/logo.png" alt="Dometic CFX" width="470">
  </picture>
</p>

# Dometic CFX for Home Assistant

Native Home Assistant custom integration for the CFX generations supported by Dometic Mobile Cooling 2.0.32: **CFX2, CFX3 and CFX5**. It discovers a nearby cooler automatically, connects locally over Bluetooth, selects DDM1 or DDM2 from the verified GATT service, detects its product type and compartment count, and creates the matching entities without YAML configuration.

> **Status.** CFX5 (`MC1`) is validated on real hardware (a CFX5 25, plus a
> CFX5 35 via the ESPHome sister project). CFX2 and CFX3 are implemented from
> the reverse-engineered app but still need a hardware test. The most important
> practical finding: connect through an **ESPHome Bluetooth proxy**, not a
> local adapter — see [Bluetooth source](#bluetooth-source) below.

## Supported families

| Family | App ID | BLE protocol | Status |
|---|---|---|---|
| CFX5 | `MC1` | DDM2 | Validated on hardware (CFX5 25; 35 via ESPHome fork) |
| CFX2 | `MC2` / `MC3` | DDM2 | Implemented, hardware test pending |
| CFX3 | `CFX3` | DDM1 | Implemented, hardware test pending |

The body-style suffixes (single zone, `IM` ice maker, `DZ` dual zone) appear
across generations — e.g. a 75DZ exists as both a CFX3 and a CFX5 — so the
suffix names a build, not a generation. Detection keys off the firmware ID
(`MC1`/`MC2`/`MC3`/`CFX3`), not the marketing name.

The integration keeps its original internal domain `dometic_cfx5` so installations of the first CFX5-only preview remain compatible.

## Why this implementation is different

This integration was reverse-engineered from Mobile Cooling to match the app's
behaviour exactly. The earlier ESPHome implementation also controls a CFX5;
the differences below were derived from the app to avoid a communication fault
that undisciplined subscriptions could trigger:

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
- Selectable Bluetooth source (automatic, a specific ESPHome proxy, or a local
  adapter), changeable at setup and later in the options

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

## Bluetooth source

The integration needs a Bluetooth source that supports active connections —
either a local adapter or an ESPHome Bluetooth proxy. **Which one matters a
great deal for the CFX.**

With a **local adapter (BlueZ)**, the encrypted bond does not reliably survive
a Home Assistant restart on some setups: after a reboot the cooler disconnects
and refuses to reconnect until it is put back into PAIR mode. This was traced
to a BlueZ/kernel-level behaviour (the link key is lost across the restart),
not to this integration.

With an **ESPHome Bluetooth proxy**, the bonded connection is kept across a
Home Assistant restart and reconnects on its own, with no pairing mode. This
matches Home Assistant's own recommendation to use a proxy for demanding BLE
devices, and it is the setup validated here.

You can choose the source both when adding the cooler and later under the
integration's **options**:

- **Automatic** — Home Assistant picks the nearest reachable adapter or proxy.
- **A specific ESPHome proxy** — recommended; forces the connection through
  that proxy and keeps the bond across restarts.
- **A local adapter** — works, but expect to re-pair after every restart.

To run a proxy, flash any ESP32 with ESPHome's `bluetooth_proxy` (`active:
true`). It can be the same board you might otherwise use for the ESPHome
variant — you do not need both at once.

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

### Troubleshooting pairing

`AuthenticationCanceled` in the log means the cooler refused a pairing
attempt, usually because it is not in Bluetooth pairing mode or because its
internal Bluetooth state is stuck (its own bond slot still holds an old
partner). Since v0.2.20 a stale local bond is removed and re-paired
automatically, and the **Re-pair Bluetooth bond** button performs the same
recovery on demand. The proven recovery sequence for a cooler that keeps
refusing is:

1. Disconnect the cooler from power for a few seconds and reconnect it.
   This clears its stuck Bluetooth state; its settings and bond table
   survive, and a factory reset is normally not needed.
2. Put the cooler into Bluetooth pairing mode (hold its Bluetooth button
   until the symbol blinks). Pairing mode times out after a few minutes,
   so continue immediately.
3. Press the **Re-pair Bluetooth bond** button (or reload the integration).
   A successful bond logs `bonded successfully with BlueZ` within seconds.

Note that with a **local BlueZ adapter** the bond is not reliably kept across
a Home Assistant restart on some setups, so pairing mode may be required again
after each reboot; an **ESPHome proxy** avoids this (see
[Bluetooth source](#bluetooth-source)). In all cases, make sure no other
central is using the cooler: do not run the Dometic app or an ESPHome client at
the same time, since the CFX serves only one connection.

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
