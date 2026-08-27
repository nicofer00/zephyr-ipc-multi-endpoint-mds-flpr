# Zephyr IPC multi_endpoint — MDS + FLPR + OTA + SPI flash (nRF54LM20B)

Standalone copy of Zephyr `samples/subsys/ipc/ipc_service/multi_endpoint`, with:

- Bluetooth Memfault Diagnostic Service (MDS) on cpuapp
- FLPR `icmsg_me` endpoints `flpr_log` / `flpr_ctrl`
- Dual USB CDC (shell on CDC0, FLPR logs on CDC1)
- Settings over ZMS
- **MCUboot + BLE MCUmgr OTA** (primary + secondary slots on **internal RRAM**)
- **SPI00 MX25R64 shell CLI** (bring-up / speed tests; not used as DFU secondary)

## Prerequisites

- nRF Connect SDK **v3.4.0** installed (e.g. `C:\ncs`)
- `nrfutil toolchain-manager` with that NCS version
- nRF54LM20 DK; J-Link serial (example: `1051800018`)
- For SPI CLI: DK **board controller** must route flash GPIOs (P2.00–P2.05) to the SoC

No freestanding `west.yml` workspace is required — build against your existing NCS tree.

## Build / flash (nrfutil)

From any directory (PowerShell):

```powershell
$APP = "C:\Users\momom\dev\sandbox\zephyr-ipc-multi-endpoint-mds-flpr"
$BUILD = "$APP\build"

nrfutil toolchain-manager launch --ncs-version=v3.4.0 --chdir C:\ncs -- `
  west build -p -b nrf54lm20dk/nrf54lm20b/cpuapp -d $BUILD $APP --sysbuild `
  -- -Dzephyr-ipc-multi-endpoint-mds-flpr_SNIPPET=nordic-flpr

nrfutil toolchain-manager launch --ncs-version=v3.4.0 --chdir C:\ncs -- `
  west flash -d $BUILD --dev-id 1051800018
```

If you symlink/copy this tree to
`zephyr/samples/subsys/ipc/ipc_service/multi_endpoint`, use
`-Dmulti_endpoint_SNIPPET=nordic-flpr` instead.

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

CLI erase/write/read use offset `0xff000` on the MX25. Rates above 8 MHz may
need high-drive GPIOs; validate with `spiflash id` / `spiflash test`.

## Make a single app+FLPR OTA image

After a successful sysbuild:

```powershell
nrfutil toolchain-manager launch --ncs-version=v3.4.0 --chdir C:\ncs -- `
  python $APP\scripts\make_app_update.py --build-dir $BUILD
```

Produces `$BUILD\dfu\app_update.bin` (cpuapp MCUboot image for BLE OTA) and
`app_and_flpr_merged.hex`. With `nordic-flpr`, FLPR RRAM sits outside the
MCUboot slots, so OTA updates the **app** image; reflash the remote image when
FLPR changes. Slot size for optional re-sign is `0xE6000` (920 KiB).

## Local BLE OTA (smpmgr)

```text
smpmgr --transport ble --address <bd_addr> image upload <path>\app_update.bin
smpmgr --transport ble --address <bd_addr> image list
smpmgr --transport ble --address <bd_addr> image test <hash>
smpmgr --transport ble --address <bd_addr> reset
```

(Exact confirm/test commands depend on your `smpmgr` version; Device Manager UI
can also upload the same `app_update.bin` over BLE SMP.)

## Cloud / Device Manager path

1. Align firmware version with Memfault / nRF Cloud release (`1.0.0+0` here;
   bump both Kconfig version symbols together).
2. Upload signed `app_update.bin` as a release for this hardware/software type.
3. Phone: **nRF Connect Device Manager** → check for updates → SMP upload.
4. MDS carries diagnostics/version into the fleet UI; MDS alone does **not**
   transfer the firmware blob.

Project key is set in `prj.conf` as `CONFIG_MEMFAULT_NCS_PROJECT_KEY`.

## just-style command notes (optional)

```text
just build   # nrfutil … west build … -D…_SNIPPET=nordic-flpr
just flash   # west flash --dev-id …
just dfu     # python scripts/make_app_update.py --build-dir build
just ota     # smpmgr image upload build/dfu/app_update.bin
```

## Upstream

- In-tree reference: `zephyr/samples/subsys/ipc/ipc_service/multi_endpoint`
- Freestanding Iconic app: https://github.com/nicofer00/nrf54lm20-mds-flpr
