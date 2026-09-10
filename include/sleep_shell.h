/*
 * Copyright (c) 2026 Nordic Semiconductor ASA
 * SPDX-License-Identifier: Apache-2.0
 */

#ifndef SLEEP_SHELL_H_
#define SLEEP_SHELL_H_

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/** True while sleep idle/off prep has quiesced LED blink / BAS churn. */
bool sleep_shell_is_quiesced(void);

/**
 * Notify sleep idle wait of a DK button event (call from button_handler).
 * @param buttons Bitmask of newly pressed buttons (e.g. DK_BTN1_MSK).
 */
void sleep_shell_button_notify(uint32_t buttons);

/** Print decoded hwinfo reset cause (also used early at boot). */
void sleep_shell_print_reset_cause(void);

#ifdef __cplusplus
}
#endif

#endif /* SLEEP_SHELL_H_ */
