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
  python -m pip install smpmgr
  ```

  `smpmgr` is the CLI used by `just usb-dfu` and the README BLE examples.
  It depends on `smpclient` and `pyserial` (pulled in automatically).

  If you only need the library API: `python -m pip install smpclient`.
  `scripts/usb_dfu.py` falls back to `smpclient` when `smpmgr` is absent.

## Commands

From this project directory:

```text
just setup              # verify shared NCS west root (no download into repo)
just build              # pristine sysbuild, NCS v3.4.0
just build v3.4.0       # explicit toolchain/SDK version
just flash
just dfu                # -> build/dfu/app_update.bin
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

## Coupled BLE OTA (smpmgr)

After `just build`, sysbuild also runs `make_app_update.py` and writes
`build/dfu/app_update.bin` (merged app + FLPR, signed for slot0). Or run
`just dfu` after an incremental build.

Requires `smpmgr` (see Prerequisites). Coupled images are signed with **SHA512**
MCUboot TLV (required on nRF54L). Older `smpmgr` only looks for SHA256 locally;
use `--format any` so the device validates the image:

```text
smpmgr --ble <bd_addr> upgrade build/dfu/app_update.bin --format any
```

(`pip install -U smpmgr` adds SHA512-aware local inspection, but `--format any`
still works on all versions.)

Or step-by-step:

```text
smpmgr --ble <bd_addr> image upload build/dfu/app_update.bin --format any
smpmgr --ble <bd_addr> image state-write <hash>
smpmgr --ble <bd_addr> os reset
```

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

Align `CONFIG_MCUBOOT_IMGTOOL_SIGN_VERSION` / `CONFIG_MEMFAULT_NCS_FW_VERSION`,
upload `app_update.bin`, use nRF Connect Device Manager over BLE SMP. MDS does
not carry the firmware blob. Project key: `CONFIG_MEMFAULT_NCS_PROJECT_KEY`.

## Upstream

- In-tree reference: `zephyr/samples/subsys/ipc/ipc_service/multi_endpoint`
- Freestanding Iconic app: https://github.com/nicofer00/nrf54lm20-mds-flpr

