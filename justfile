# OS-agnostic recipes for this sample (run from the project directory).
# West always runs inside the *shared* NCS install from nrfutil Ã¢â‚¬â€ this repo
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
    {{ launch }} {{ version }} {{ py }} {{ app_dir }}/scripts/ble_dfu.py --image {{ build_dir }}/dfu/app_update.bin {{ ble_dfu_ble_args }}