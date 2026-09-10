/*
 * Copyright (c) 2026 Nordic Semiconductor ASA
 * SPDX-License-Identifier: Apache-2.0
 */

#ifndef USB_CDC_TRIPLE_H_
#define USB_CDC_TRIPLE_H_

#ifdef __cplusplus
extern "C" {
#endif

/** Disable the triple-CDC USBD stack (for System OFF entry). */
int usb_cdc_triple_disable(void);

#ifdef __cplusplus
}
#endif

#endif /* USB_CDC_TRIPLE_H_ */
