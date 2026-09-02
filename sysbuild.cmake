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

# Package coupled app+FLPR DFU artifact after sysbuild images are ready.
# DEFAULT_IMAGE is the basename of the app source dir (clone folder name).
add_custom_command(
  OUTPUT ${CMAKE_BINARY_DIR}/dfu/app_update.bin
  COMMAND ${PYTHON_EXECUTABLE}
          ${APP_DIR}/scripts/make_app_update.py
          --build-dir ${CMAKE_BINARY_DIR}
  DEPENDS ${DEFAULT_IMAGE}_extra_byproducts remote_extra_byproducts
  WORKING_DIRECTORY ${CMAKE_BINARY_DIR}
  COMMENT "Packaging coupled app+FLPR OTA artifact (Strategy B)"
)

add_custom_target(coupled_dfu_package ALL
  DEPENDS ${CMAKE_BINARY_DIR}/dfu/app_update.bin
)