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
    {{ launch }} {{ version }} west build -p -b {{ board }} -d {{ build_dir }} {{ app_dir }} --sysbuild -- -D{{ snippet }}_SNIPPET=nordic-flpr {{ args }}

build-incr version="v3.4.0" *args:
    {{ launch }} {{ version }} west build -b {{ board }} -d {{ build_dir }} {{ app_dir }} --sysbuild -- -D{{ snippet }}_SNIPPET=nordic-flpr {{ args }}

flash version="v3.4.0" *args:
    {{ launch }} {{ version }} west flash -d {{ build_dir }} --dev-id {{ jlink_sn }} {{ args }}

dfu version="v3.4.0":
    {{ launch }} {{ version }} {{ py }} {{ app_dir }}/scripts/make_app_update.py --build-dir {{ build_dir }}
