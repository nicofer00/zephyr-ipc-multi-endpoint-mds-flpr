# OS-agnostic recipes for this sample (run from the project directory).
# West always runs inside the *shared* NCS install from nrfutil — this repo
# is not a west workspace and does not vendor Zephyr/NCS.
#
# Use forward-slash paths in recipes: on Windows, just/nrfutil eat backslashes.

py := if os() == "windows" { "python" } else { "python3" }

# justfile_directory() may use '\'; normalize before passing to nrfutil.
app_dir := replace(justfile_directory(), "\\", "/")
build_dir := app_dir + "/build"
snippet := file_name(justfile_directory())
launch_py := app_dir + "/scripts/ncs_launch.py"

board := env("BOARD", "nrf54lm20dk/nrf54lm20b/cpuapp")
jlink_sn := env("JLINK_SN", "1051800018")

launch := py + " " + launch_py

[private]
default:
    @just --list

# Verify shared NCS toolchain + west workspace (no download into this repo).
setup version="v3.4.0":
    {{ launch }} {{ version }} --doctor

doctor version="v3.4.0":
    {{ launch }} {{ version }} --doctor

ncs-root version="v3.4.0":
    {{ launch }} {{ version }} --print-root

# Run any west command in the shared NCS workspace.
west version="v3.4.0" *args:
    {{ launch }} {{ version }} west {{ args }}

# Pristine sysbuild (app + FLPR + MCUboot). Example: just build   or   just build v3.3.0
build version="v3.4.0" *args:
    {{ launch }} {{ version }} west build -p -b {{ board }} -d {{ build_dir }} {{ app_dir }} --sysbuild -- -D{{ snippet }}_SNIPPET="nordic-flpr;mds-flpr" {{ args }}

build-incr version="v3.4.0" *args:
    {{ launch }} {{ version }} west build -b {{ board }} -d {{ build_dir }} {{ app_dir }} --sysbuild -- -D{{ snippet }}_SNIPPET="nordic-flpr;mds-flpr" {{ args }}

# Same as build, but printk/boot on RTT; uart20+uart30 disabled; shell/LOG/DFU on USB CDC.
rtt_args := ("-D" + snippet + "_EXTRA_CONF_FILE=" + app_dir + "/overlay-rtt.conf "
    + "-D" + snippet + "_EXTRA_DTC_OVERLAY_FILE=" + app_dir + "/boards/nrf54lm20dk_nrf54lm20b_cpuapp_rtt.overlay "
    + "-Dremote_EXTRA_CONF_FILE=" + app_dir + "/remote/overlay-rtt.conf "
    + "-Dremote_EXTRA_DTC_OVERLAY_FILE=" + app_dir + "/remote/boards/nrf54lm20dk_nrf54lm20b_cpuflpr_rtt.overlay")

build-rtt version="v3.4.0" *args:
    {{ launch }} {{ version }} west build -p -b {{ board }} -d {{ build_dir }} {{ app_dir }} --sysbuild -- -D{{ snippet }}_SNIPPET="nordic-flpr;mds-flpr" {{ rtt_args }} {{ args }}

build-rtt-incr version="v3.4.0" *args:
    {{ launch }} {{ version }} west build -b {{ board }} -d {{ build_dir }} {{ app_dir }} --sysbuild -- -D{{ snippet }}_SNIPPET="nordic-flpr;mds-flpr" {{ rtt_args }} {{ args }}

flash version="v3.4.0" *args:
    {{ launch }} {{ version }} west flash -d {{ build_dir }} --dev-id {{ jlink_sn }} {{ args }}

# Dual-pane SEGGER RTT viewer (cpuapp M33 + FLPR RV32) via pylink-square.
# Requires: python -m pip install pylink-square rich
# Env: JLINK_SN (default below). Extra args: just rtt -- --poll-ms 10
rtt *args:
    {{ py }} {{ app_dir }}/scripts/dual_rtt.py --sn {{ jlink_sn }} {{ args }}

dfu version="v3.4.0":
    {{ launch }} {{ version }} {{ py }} {{ app_dir }}/scripts/make_app_update.py --build-dir {{ build_dir }}

# Package coupled image, then upload+test+reset over USB CDC2 (MI_04 / uart-mcumgr).
# Port: auto-detect VID 2FE3 MI_04, or USB_DFU_PORT=COM12 just usb-dfu
usb_dfu_port_args := if env("USB_DFU_PORT", "") != "" { "--port " + env("USB_DFU_PORT") } else { "" }

usb-dfu version="v3.4.0":
    {{ launch }} {{ version }} {{ py }} {{ app_dir }}/scripts/make_app_update.py --build-dir {{ build_dir }}
    {{ launch }} {{ version }} {{ py }} {{ app_dir }}/scripts/usb_dfu.py --image {{ build_dir }}/dfu/app_update.bin {{ usb_dfu_port_args }}

# Package coupled image, then upload+test+reset over BLE SMP.
# Address: BLE_DFU_ADDR=AA:BB:CC:DD:EE:FF just ble-dfu
ble_dfu_ble_args := if env("BLE_DFU_ADDR", "") != "" { "--ble " + env("BLE_DFU_ADDR") } else { "" }

ble-dfu version="v3.4.0":
    {{ launch }} {{ version }} {{ py }} {{ app_dir }}/scripts/make_app_update.py --build-dir {{ build_dir }}
    {{ launch }} {{ version }} {{ py }} {{ app_dir }}/scripts/ble_dfu.py --image {{ build_dir }}/dfu/app_update.bin {{ ble_dfu_ble_args }} --skip-pair

# SBOM for CVE scanners (grype / GitLab): pristine build → west ncs-sbom (per sysbuild
# domain, PURLs/CPEs) → syft merge to build/sbom/spdx.json.
# Needs `syft` on PATH (Chocolatey) or: python -m pip install -r scripts/requirements-sbom.txt
# (anchore_syft 1.51.1 wheel branch; Windows sdist needs MSVC — prefer PATH syft there).
# Omit scancode-toolkit (not in nrfutil toolchain; optional for CVE/PURL SBOMs). For full
# license scanning: nrfutil toolchain-manager launch --ncs-version=v3.4.0 -- \
#   pip3 install -r nrf/scripts/requirements-west-ncs-sbom.txt
# ncs-sbom can still take several minutes.
ncs_sbom_detectors := "spdx-tag,full-text,external-file,git-info"

sbom version="v3.4.0" *args:
    {{ launch }} {{ version }} west build -p -b {{ board }} -d {{ build_dir }} {{ app_dir }} --sysbuild -- -D{{ snippet }}_SNIPPET="nordic-flpr;mds-flpr" {{ args }}
    {{ py }} -c "from pathlib import Path; Path(r'{{ build_dir }}/sbom').mkdir(parents=True, exist_ok=True)"
    {{ launch }} {{ version }} west ncs-sbom -d {{ build_dir }} --license-detectors {{ ncs_sbom_detectors }} --output-spdx {{ build_dir }}/sbom/sbom_{domain}.spdx
    {{ py }} {{ app_dir }}/scripts/sbom_syft.py --build-dir {{ build_dir }} --name {{ snippet }}

# Reuse an existing build: west ncs-sbom + syft merge only (no rebuild).
sbom-from-build version="v3.4.0":
    {{ py }} -c "from pathlib import Path; Path(r'{{ build_dir }}/sbom').mkdir(parents=True, exist_ok=True)"
    {{ launch }} {{ version }} west ncs-sbom -d {{ build_dir }} --license-detectors {{ ncs_sbom_detectors }} --output-spdx {{ build_dir }}/sbom/sbom_{domain}.spdx
    {{ py }} {{ app_dir }}/scripts/sbom_syft.py --build-dir {{ build_dir }} --name {{ snippet }}

# CVE triage against the merged SBOM (relative path for Windows sbom: scheme).
grype-sbom:
    grype sbom:build/sbom/spdx.json
