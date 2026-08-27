# Zephyr IPC multi_endpoint — MDS + FLPR + OTA + SPI flash (nRF54LM20B)

Standalone copy of Zephyr `samples/subsys/ipc/ipc_service/multi_endpoint`, with:

- Bluetooth Memfault Diagnostic Service (MDS) on cpuapp
- FLPR `icmsg_me` endpoints `flpr_log` / `flpr_ctrl`
- Dual USB CDC (shell on CDC0, FLPR logs on CDC1)
- Settings over ZMS
- **MCUboot + BLE MCUmgr OTA** (primary + secondary slots on **internal RRAM**)
- **SPI00 MX25R64 shell CLI** (bring-up / speed tests; not used as DFU secondary)

## Prerequisites

- [just](https://github.com/casey/just)
- Python 3 on `PATH` (`python` on Windows, `python3` on Unix)
- `nrfutil` with **toolchain-manager** and NCS **v3.4.0** installed
- nRF54LM20 DK (set `JLINK_SN` if needed)
- For SPI CLI: DK **board controller** must route flash GPIOs (P2.00–P2.05) to the SoC

No freestanding `west.yml` workspace is required. The justfile resolves the NCS
west root from `nrfutil toolchain-manager list --json` (parent of `toolchains/`).

## Build / flash / DFU

From this project directory:

```text
just build                          # pristine sysbuild, NCS v3.4.0
just build v3.4.0                   # same, explicit toolchain version arg
just flash                          # west flash --dev-id $JLINK_SN
just dfu                            # scripts/make_app_update.py → build/dfu/
just ncs-root                       # print west root from nrfutil JSON
just --list
```

Optional env overrides:

```text
BOARD=nrf54lm20dk/nrf54lm20b/cpuapp
JLINK_SN=1051800018
```

Snippet CMake flag is derived from the directory name (`multi_endpoint` vs
`zephyr-ipc-multi-endpoint-mds-flpr`).

## Ports (typical on LM20 DK)

| Port | Role |
|------|------|
| Debugger uart20 | App `printk` / LOG |
| Debugger uart30 | FLPR local `printk` |
| USB CDC0 | Shell (`flpr`, `spiflash`, `memfault`, …) |
| USB CDC1 | Forwarded FLPR logs |

## SPI00 flash CLI

DTS keeps **`spi-max-frequency = 8 MHz`**. The shell keeps a RAM `spi_config`
so you can raise the **CLI** clock up to **32 MHz** (SPIM00 high-speed limit on P2):

```text
spiflash info
spiflash speed
spiflash speed 32000000
spiflash id
spiflash test
```

CLI erase/write/read use offset `0xff000` on the MX25.

## Local BLE OTA (smpmgr)

After `just dfu`:

```text
smpmgr --transport ble --address <bd_addr> image upload build/dfu/app_update.bin
smpmgr --transport ble --address <bd_addr> image list
smpmgr --transport ble --address <bd_addr> image test <hash>
smpmgr --transport ble --address <bd_addr> reset
```

With `nordic-flpr`, FLPR RRAM sits outside the MCUboot slots, so OTA updates the
**app** image; reflash the remote image when FLPR changes.

## Cloud / Device Manager path

1. Align firmware version with Memfault / nRF Cloud release (`1.0.0+0` here;
   bump both Kconfig version symbols together).
2. Upload signed `app_update.bin` as a release for this hardware/software type.
3. Phone: **nRF Connect Device Manager** → check for updates → SMP upload.
4. MDS carries diagnostics/version into the fleet UI; MDS alone does **not**
   transfer the firmware blob.

Project key is set in `prj.conf` as `CONFIG_MEMFAULT_NCS_PROJECT_KEY`.

## Upstream

- In-tree reference: `zephyr/samples/subsys/ipc/ipc_service/multi_endpoint`
- Freestanding Iconic app: https://github.com/nicofer00/nrf54lm20-mds-flpr
