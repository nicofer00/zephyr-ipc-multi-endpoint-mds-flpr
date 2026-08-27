# OS-agnostic recipes for this sample (run from the project directory).
# NCS west workspace is discovered from: nrfutil toolchain-manager list --json

# Prefer python3 on Unix; python on Windows.
py := if os() == "windows" { "python" } else { "python3" }

app_dir := justfile_directory()
build_dir := app_dir / "build"
# Sysbuild image / snippet prefix follows the directory name.
snippet := file_name(app_dir)

board := env("BOARD", "nrf54lm20dk/nrf54lm20b/cpuapp")
jlink_sn := env("JLINK_SN", "1051800018")

launch := py + " " + quote(app_dir / "scripts" / "ncs_launch.py")

[private]
default:
    @just --list

# Pristine sysbuild (app + FLPR + MCUboot). Example: just build   or   just build v3.3.0
build version="v3.4.0" *args:
    {{ launch }} {{ version }} west build -p -b {{ board }} -d {{ build_dir }} {{ app_dir }} --sysbuild -- -D{{ snippet }}_SNIPPET=nordic-flpr {{ args }}

# Incremental rebuild (no -p).
build-incr version="v3.4.0" *args:
    {{ launch }} {{ version }} west build -b {{ board }} -d {{ build_dir }} {{ app_dir }} --sysbuild -- -D{{ snippet }}_SNIPPET=nordic-flpr {{ args }}

# Flash MCUboot + remote + signed app.
flash version="v3.4.0" *args:
    {{ launch }} {{ version }} west flash -d {{ build_dir }} --dev-id {{ jlink_sn }} {{ args }}

# Create dfu/app_update.bin (+ merged hex) from the last build.
dfu version="v3.4.0":
    {{ launch }} {{ version }} {{ py }} {{ app_dir / "scripts" / "make_app_update.py" }} --build-dir {{ build_dir }}

# Show NCS west root resolved from nrfutil for this toolchain version.
ncs-root version="v3.4.0":
    {{ launch }} {{ version }} --print-root
