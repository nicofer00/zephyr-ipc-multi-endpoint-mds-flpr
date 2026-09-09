# Zephyr IPC multi_endpoint — MDS + FLPR + OTA + SPI flash (nRF54LM20B)

Standalone copy of Zephyr `samples/subsys/ipc/ipc_service/multi_endpoint`, with:

- Bluetooth Memfault Diagnostic Service (MDS) on cpuapp
- FLPR `icmsg_me` endpoints `flpr_log` / `flpr_ctrl`
- Triple USB CDC (shell CDC0, FLPR logs CDC1, UART SMP DFU CDC2)
- Settings over ZMS
- **MCUboot + BLE / USB CDC MCUmgr OTA** — **coupled Strategy B**: one signed `app_update.bin` updates **cpuapp + FLPR** atomically (in-slot FLPR payload @ `0xE8000`, `nordic_vpr_launcher` boot path)
- **SPI00 MX25R64 shell CLI** (bring-up / speed tests; not used as DFU secondary)

## Important: this repo is not a west workspace

Do **not** run bare `west` here, and do **not** `west init` / `west update` inside
this clone (that would pull NCS into the project).

Build commands use `just`, which runs `west` inside your **existing shared NCS
install** (from nrfutil’s install directory, typically with a `.west` next to
`toolchains/`). Only build artifacts land under `./build` in this repo.

## Prerequisites

- [just](https://github.com/casey/just)
- Python 3 on `PATH` (`python` on Windows, `python3` on Unix)
- `nrfutil` + **toolchain-manager**
- A full NCS SDK **once**, shared (outside this repo), matching the toolchain
  version (default **v3.4.0**). If `just setup` says sources are missing:

  ```text
  nrfutil sdk-manager install v3.4.0
  ```

  or install SDK v3.4.0 via nRF Connect for Desktop → Toolchain Manager.

- nRF54LM20 DK (override with `JLINK_SN` if needed)
- For SPI CLI: DK board controller must route flash GPIOs (P2.00-P2.05) to the SoC
- **SMP host tools** (BLE OTA / USB CDC DFU), installed into a Python 3.10+ environment
  (the NCS toolchain Python from `nrfutil` works):

  ```text
  python -m pip install 'smpmgr>=0.19' 'smpclient[ble]'
  ```

  `smpmgr` is the CLI used by the README BLE examples. **Use smpmgr 0.19+** for
  nRF54L coupled images (MCUboot **SHA512** TLV). Older smpmgr only inspects
  SHA256 and fails before upload.

  `just ble-dfu` and `just usb-dfu` avoid that host check (smpclient fallback or
  `smpmgr upgrade --format any` when available).

  If you only need the library API: `python -m pip install 'smpclient[ble]'`.
  `scripts/usb_dfu.py` and `scripts/ble_dfu.py` fall back to smpclient when needed.

## Commands

From this project directory:

```text
just setup              # verify shared NCS west root (no download into repo)
just build              # pristine sysbuild, NCS v3.4.0
just build v3.4.0       # explicit toolchain/SDK version
just flash
just dfu                # -> build/dfu/app_update.bin
just ble-dfu            # package + upload/test/reset over BLE
just usb-dfu            # package + upload/test/reset on USB CDC2
just west list          # any west command in the shared workspace
just ncs-root           # print shared west topdir
just --list
```

Optional env overrides: `BOARD`, `JLINK_SN`.

Snippet chain: **`nordic-flpr;mds-flpr`** (local `mds-flpr` snippet sets explicit
968 KiB MCUboot slots and in-slot FLPR payload partition).

## Ports (typical on LM20 DK)

| Port | Role |
|------|------|
| Debugger uart20 | App `printk` / LOG (default build) |
| Debugger uart30 | FLPR local `printk` (default build) |
| USB CDC0 | Shell (`flpr`, `spiflash`, `memfault`, …) + shell LOG backend |
| USB CDC1 | Forwarded FLPR logs |
| USB CDC2 | MCUmgr UART SMP DFU (same `app_update.bin` as BLE) |

### Optional: RTT boot + free uart20/uart30

```text
just build-rtt
just flash
```

Uses `overlay-rtt.conf` + `*_rtt.overlay` (app and remote): disables **uart20** / **uart30**,
sends `printk` / early boot to **SEGGER RTT** (non-blocking, no USB wait), keeps shell,
shell LOG, FLPR IPC logs, and DFU on the three USB CDCs. View boot with J-Link RTT Viewer
(SWD, auto control-block detect) while CDC hosts remain independent.

## SPI00 flash CLI

DTS keeps **`spi-max-frequency = 8 MHz`**. Raise CLI speed up to **32 MHz**:

```text
spiflash info
spiflash speed 32000000
spiflash id
spiflash test
```

## Versioning (Zephyr VERSION file)

Firmware and coupled DFU packages share one version source: the sample
[`VERSION`](VERSION) file (Zephyr `APP_VERSION_TWEAK_STRING` /
`CONFIG_MCUBOOT_IMGTOOL_SIGN_VERSION`).

```text
VERSION_MAJOR = 1
VERSION_MINOR = 0
PATCHLEVEL = 1
VERSION_TWEAK = 0
EXTRAVERSION =
```

Bump those fields, then `just build-incr` (or pristine `just build`) and
`just dfu` / `just usb-dfu` / `just ble-dfu`. `make_app_update.py` reads
`VERSION` and signs with imgtool as `MAJOR.MINOR.PATCH+TWEAK` (same as
Zephyr `APP_VERSION_TWEAK_STRING` / `CONFIG_MCUBOOT_IMGTOOL_SIGN_VERSION`).
`EXTRAVERSION` is kept for app/build metadata only — imgtool rejects
strings like `1.0.0-test+1`.

## Coupled BLE OTA (smpmgr)

After `just build`, sysbuild also runs `make_app_update.py` and writes
`build/dfu/app_update.bin` (merged app + FLPR, signed for slot0). That step
re-runs when app/FLPR images, sample `VERSION`, the packaging script, or the
MCUboot signing key change. Or run `just dfu` after an incremental build.

Requires `smpmgr>=0.19` (see Prerequisites). Coupled images are signed with
**SHA512** MCUboot TLV (required on nRF54L). **smpmgr before 0.18** has no
`--format` option and only checks SHA256 — upgrade smpmgr or use `just ble-dfu`.

**Recommended** (smpmgr 0.19+). Prefer the **advertised name**. Advertising uses
the stable identity address (`BT_LE_ADV_OPT_USE_IDENTITY`) so nRF Connect /
Memfault see the same MAC after disconnect (privacy RPA rotation alone looks
like a new device). Name scan still avoids stale host caches:

```text
smpmgr --ble Nordic_Memfault --timeout 300 upgrade build/dfu/app_update.bin
```

Debug log + connection probe (no upload):

```text
python scripts/ble_dfu.py --probe --logfile smpmgr_ble_debug.log
# or:
smpmgr --ble Nordic_Memfault --timeout 120 --loglevel DEBUG --logfile smpmgr_ble_debug.log image state-read
```

**Or** use the helper (quiet progress bar, auto-scan by name, 300 s timeout):

```text
just dfu
just ble-dfu
# or: python scripts/ble_dfu.py
# verbose bleak logs: python scripts/ble_dfu.py -v --logfile smpmgr_ble_debug.log
# smpmgr CLI instead:  python scripts/ble_dfu.py --smpmgr
```

`smpmgr` defaults to `--timeout 2` — far too short for a ~900 KiB image and
secondary-slot erase. Always pass `--timeout 300` (or higher) for BLE upgrade.

**`BleakGATTProtocolError: Insufficient Authentication` (error 5):** SMP
**notify** (CCC) needs a Windows BLE bond. This sample sets
`CONFIG_BT_SMP_ENFORCE_MITM=n` so Just Works pairing works (encrypted link,
no display/keyboard). That is not open/unencrypted SMP.

1. Flash the current firmware (`just flash`)
2. DK shell: `bt clear all` (clears stale device bonds)
3. Windows Settings → Bluetooth: remove old **Nordic_Memfault**, then pair it
   (or let `just ble-dfu` call bleak `pair()`)
4. `python scripts/ble_dfu.py --probe` or `just ble-dfu`

For **USB DFU** (no BLE pairing):

```text
just usb-dfu
```

`just usb-dfu` / `just ble-dfu` default to **test** swap (safe rollback). After
reset, the new image must be **confirmed** or MCUboot keeps the old image in
the secondary slot for revert — the next upload then fails with
`IMG_MGMT_ERR.NO_FREE_SLOT`.

This sample self-confirms in `main()` via `boot_write_img_confirmed()`. The DFU
scripts also confirm the active image and erase slot1 before each upload so
repeated A/B updates work even on a device stuck in test/revert.

Use `--confirm` on the DFU scripts only if you want a permanent swap without
the one-boot test window.

## Coupled USB CDC DFU (CDC2)

Same coupled `app_update.bin` over serial SMP on **USB CDC2** (VID `2FE3` / PID `0005`):

| Interface | Typical Windows COM | Role |
|-----------|---------------------|------|
| MI_00 | first CDC (e.g. COM10) | Shell |
| MI_02 | second (e.g. COM11) | FLPR logs |
| MI_04 | third (e.g. COM12) | **MCUmgr UART SMP** |

Package and upgrade (upload, mark for test swap, reset):

```text
just usb-dfu
```

Auto-detects the MI_04 port. Override if needed:

```text
# Windows PowerShell
$env:USB_DFU_PORT = "COM12"; just usb-dfu

# Unix
USB_DFU_PORT=/dev/ttyACM2 just usb-dfu
```

Or package once, then call the helper / CLI directly:

```text
just dfu
python scripts/usb_dfu.py --port COM12
# or: smpmgr --port COM12 upgrade build/dfu/app_update.bin --format any
```

One SMP upload updates **both** cores — no separate FLPR flash when FLPR changes.
Use `make_app_update.py --cpuapp-only` only for debug (breaks coupled OTA).

UDC buffers for three CDCs: `CONFIG_UDC_BUF_COUNT=48`, `CONFIG_UDC_BUF_POOL_SIZE=12288`.

Memory map (nRF54LM20B): 968 KiB slots, FLPR storage copy inside slot0 @
`0xE8000` (96 KiB), launcher copies to FLPR SRAM @ `0x20067c00` at boot.

## Cloud / Device Manager

Align the sample [`VERSION`](VERSION) file (MCUboot sign version + Memfault
static FW version), upload `app_update.bin`, use nRF Connect Device Manager
over BLE SMP. MDS does not carry the firmware blob. Project key:
`CONFIG_MEMFAULT_NCS_PROJECT_KEY`.

## Upstream

- In-tree reference: `zephyr/samples/subsys/ipc/ipc_service/multi_endpoint`
- Freestanding Iconic app: https://github.com/nicofer00/nrf54lm20-mds-flpr

