# Zephyr IPC multi_endpoint — MDS + FLPR (nRF54LM20B)

Standalone copy of the Zephyr sample
`samples/subsys/ipc/ipc_service/multi_endpoint`, evolved for:

- Bluetooth Memfault Diagnostic Service (MDS) on cpuapp
- FLPR `icmsg_me` endpoints `flpr_log` / `flpr_ctrl`
- Dual USB CDC on the nRF54LM20 DK (shell on CDC0, FLPR logs on CDC1)
- Settings over ZMS for bonding/CCC persistence

## Build (inside an NCS v3.4.0 west workspace)

Place or symlink this tree under your Zephyr `samples/...` path, or set it as
the application directory:

```bash
west build -p -b nrf54lm20dk/nrf54lm20b/cpuapp --sysbuild \
  path/to/zephyr-ipc-multi-endpoint-mds-flpr \
  -- -Dmulti_endpoint_SNIPPET=nordic-flpr
```

Sysbuild image name follows the app directory name; if the folder is not
`multi_endpoint`, pass the matching snippet flag, e.g.
`-Dzephyr-ipc-multi-endpoint-mds-flpr_SNIPPET=nordic-flpr`.

Flash:

```bash
west flash --dev-id <jlink-serial>
```

See `README.rst` for ports and shell commands.

Upstream reference: [zephyr/samples/subsys/ipc/ipc_service/multi_endpoint](https://github.com/nrfconnect/sdk-zephyr/tree/main/samples/subsys/ipc/ipc_service/multi_endpoint)

For a freestanding Iconic/west workspace app, see also
https://github.com/nicofer00/nrf54lm20-mds-flpr
