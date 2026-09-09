# Copyright (c) 2023 Nordic Semiconductor ASA
# SPDX-License-Identifier: Apache-2.0

list(APPEND SNIPPET_ROOT ${APP_DIR})
set(SNIPPET_ROOT ${SNIPPET_ROOT} CACHE PATH "Sample-local snippet root" FORCE)

if("${SB_CONFIG_NET_CORE_BOARD}" STREQUAL "")
  message(FATAL_ERROR "Target ${BOARD} not supported for this sample. "
    "There is no remote board selected in Kconfig.sysbuild")
endif()

ExternalZephyrProject_Add(
  APPLICATION remote
  SOURCE_DIR  ${APP_DIR}/remote
  BOARD       ${SB_CONFIG_NET_CORE_BOARD}
)

native_simulator_set_child_images(${DEFAULT_IMAGE} remote)
native_simulator_set_final_executable(${DEFAULT_IMAGE})

# Signing key used by make_app_update.py (same discovery as the script / MCUboot).
set(_COUPLED_DFU_KEY "")
if(DEFINED SB_CONFIG_BOOT_SIGNATURE_KEY_FILE AND NOT "${SB_CONFIG_BOOT_SIGNATURE_KEY_FILE}" STREQUAL "")
  if(EXISTS "${SB_CONFIG_BOOT_SIGNATURE_KEY_FILE}")
    set(_COUPLED_DFU_KEY "${SB_CONFIG_BOOT_SIGNATURE_KEY_FILE}")
  endif()
endif()
if(_COUPLED_DFU_KEY STREQUAL "")
  foreach(_key_cand
      "${ZEPHYR_BASE}/../bootloader/mcuboot/root-ed25519.pem"
      "${ZEPHYR_BASE}/bootloader/mcuboot/root-ed25519.pem"
      "${ZEPHYR_BASE}/../bootloader/mcuboot/root-rsa-2048.pem"
      "${ZEPHYR_BASE}/bootloader/mcuboot/root-rsa-2048.pem")
    if(EXISTS "${_key_cand}")
      get_filename_component(_COUPLED_DFU_KEY "${_key_cand}" ABSOLUTE)
      break()
    endif()
  endforeach()
endif()

set(_COUPLED_DFU_DEPENDS
  ${DEFAULT_IMAGE}_extra_byproducts
  remote_extra_byproducts
  ${APP_DIR}/VERSION
  ${APP_DIR}/scripts/make_app_update.py
)
if(NOT _COUPLED_DFU_KEY STREQUAL "")
  list(APPEND _COUPLED_DFU_DEPENDS ${_COUPLED_DFU_KEY})
endif()

# Package coupled app+FLPR DFU artifact after sysbuild images are ready.
# DEFAULT_IMAGE is the basename of the app source dir (clone folder name).
add_custom_command(
  OUTPUT ${CMAKE_BINARY_DIR}/dfu/app_update.bin
  COMMAND ${PYTHON_EXECUTABLE}
          ${APP_DIR}/scripts/make_app_update.py
          --build-dir ${CMAKE_BINARY_DIR}
  DEPENDS ${_COUPLED_DFU_DEPENDS}
  WORKING_DIRECTORY ${CMAKE_BINARY_DIR}
  COMMENT "Packaging coupled app+FLPR OTA artifact (Strategy B)"
  VERBATIM
)

add_custom_target(coupled_dfu_package ALL
  DEPENDS ${CMAKE_BINARY_DIR}/dfu/app_update.bin
)