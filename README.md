# Zephyr IPC multi_endpoint — MDS + FLPR + OTA + SPI flash (nRF54LM20B)

Standalone copy of Zephyr `samples/subsys/ipc/ipc_service/multi_endpoint`, with:

- Bluetooth Memfault Diagnostic Service (MDS) on cpuapp
- FLPR `icmsg_me` endpoints `flpr_log` / `flpr_ctrl`
- Dual USB CDC (shell on CDC0, FLPR logs on CDC1)
- Settings over ZMS
- **MCUboot + BLE MCUmgr OTA** (primary + secondary slots on **internal RRAM**)
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
- For SPI CLI: DK board controller must route flash GPIOs (P2.00–P2.05) to the SoC

## Commands

From this project directory:

```text
just setup              # verify shared NCS west root (no download into repo)
just build              # pristine sysbuild, NCS v3.4.0
just build v3.4.0       # explicit toolchain/SDK version
just flash
just dfu                # → build/dfu/app_update.bin
just west list          # any west command in the shared workspace
just ncs-root           # print shared west topdir
just --list
```

Optional env overrides: `BOARD`, `JLINK_SN`.

Snippet CMake flag follows the directory name (`multi_endpoint` vs
`zephyr-ipc-multi-endpoint-mds-flpr`).

## Ports (typical on LM20 DK)

| Port | Role |
|------|------|
| Debugger uart20 | App `printk` / LOG |
| Debugger uart30 | FLPR local `printk` |
| USB CDC0 | Shell (`flpr`, `spiflash`, `memfault`, …) |
| USB CDC1 | Forwarded FLPR logs |

## SPI00 flash CLI

DTS keeps **`spi-max-frequency = 8 MHz`**. Raise CLI speed up to **32 MHz**:

```text
spiflash info
spiflash speed 32000000
spiflash id
spiflash test
```

## Local BLE OTA (smpmgr)

After `just dfu`:

```text
smpmgr --transport ble --address <bd_addr> image upload build/dfu/app_update.bin
```

With `nordic-flpr`, FLPR RRAM sits outside MCUboot slots; OTA updates the **app**
image. Reflash remote when FLPR changes.

## Cloud / Device Manager

Align `CONFIG_MCUBOOT_IMGTOOL_SIGN_VERSION` / `CONFIG_MEMFAULT_NCS_FW_VERSION`,
upload `app_update.bin`, use nRF Connect Device Manager over BLE SMP. MDS does
not carry the firmware blob. Project key: `CONFIG_MEMFAULT_NCS_PROJECT_KEY`.

## Upstream

- In-tree reference: `zephyr/samples/subsys/ipc/ipc_service/multi_endpoint`
- Freestanding Iconic app: https://github.com/nicofer00/nrf54lm20-mds-flpr
