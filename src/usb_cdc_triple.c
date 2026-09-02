/*
 * Copyright (c) 2026 Nordic Semiconductor ASA
 * SPDX-License-Identifier: Apache-2.0
 *
 * Triple CDC ACM USB device init for this sample:
 *   CDC0 - shell (zephyr,shell-uart)
 *   CDC1 - FLPR log forward (cdc_acm_uart1)
 *   CDC2 - MCUmgr UART SMP DFU (zephyr,uart-mcumgr)
 *
 * Stock CONFIG_CDC_ACM_SERIAL_INITIALIZE_AT_BOOT only registers CDCs named by
 * console / shell-uart / uart-mcumgr. With console on uart20 that yields at most
 * two CDCs, so this file replaces the stock boot init.
 */

#include <zephyr/device.h>
#include <zephyr/init.h>
#include <zephyr/kernel.h>
#include <zephyr/usb/usbd.h>
#include <zephyr/logging/log.h>

LOG_MODULE_REGISTER(usb_cdc_triple, CONFIG_USBD_LOG_LEVEL);

#define USB_VID 0x2fe3
#define USB_PID 0x0005
#define USB_MANUFACTURER "Nordic Semiconductor"
#define USB_PRODUCT "MDS FLPR triple CDC"

USBD_DEVICE_DEFINE(cdc_acm_serial,
		   DEVICE_DT_GET(DT_NODELABEL(zephyr_udc0)),
		   USB_VID, USB_PID);

USBD_DESC_LANG_DEFINE(cdc_acm_serial_lang);
USBD_DESC_MANUFACTURER_DEFINE(cdc_acm_serial_mfr, USB_MANUFACTURER);
USBD_DESC_PRODUCT_DEFINE(cdc_acm_serial_product, USB_PRODUCT);
IF_ENABLED(CONFIG_HWINFO, (USBD_DESC_SERIAL_NUMBER_DEFINE(cdc_acm_serial_sn)));

USBD_DESC_CONFIG_DEFINE(fs_cfg_desc, "FS Configuration");
USBD_DESC_CONFIG_DEFINE(hs_cfg_desc, "HS Configuration");

USBD_CONFIGURATION_DEFINE(cdc_acm_serial_fs_config, 0, 125, &fs_cfg_desc);
USBD_CONFIGURATION_DEFINE(cdc_acm_serial_hs_config, 0, 125, &hs_cfg_desc);

/* Registration order = host COM enumeration order. */
static const struct device *const uart_devs[] = {
	DEVICE_DT_GET(DT_NODELABEL(cdc_acm_uart0)),
	DEVICE_DT_GET(DT_NODELABEL(cdc_acm_uart1)),
	DEVICE_DT_GET(DT_NODELABEL(cdc_acm_uart2)),
};

static int register_uart_cdc(struct usbd_context *const uds_ctx,
			     const enum usbd_speed speed,
			     const struct device *const dev)
{
	int err;

	if (speed == USBD_SPEED_HS) {
		STRUCT_SECTION_FOREACH_ALTERNATE(usbd_class_hs, usbd_class_node, c_nd) {
			struct usbd_class_data *c_data = c_nd->c_data;

			if (usbd_class_get_private(c_data) == dev) {
				err = usbd_register_class(&cdc_acm_serial,
							  c_data->name, speed, 1);
				if (err != 0 && err != -EALREADY) {
					LOG_ERR("Failed to register %s (%d)",
						c_data->name, err);
					return err;
				}
				break;
			}
		}
	}

	if (speed == USBD_SPEED_FS) {
		STRUCT_SECTION_FOREACH_ALTERNATE(usbd_class_fs, usbd_class_node, c_nd) {
			struct usbd_class_data *c_data = c_nd->c_data;

			if (usbd_class_get_private(c_data) == dev) {
				err = usbd_register_class(&cdc_acm_serial,
							  c_data->name, speed, 1);
				if (err != 0 && err != -EALREADY) {
					LOG_ERR("Failed to register %s (%d)",
						c_data->name, err);
					return err;
				}
				break;
			}
		}
	}

	return 0;
}

static int register_cdc_acm(struct usbd_context *const uds_ctx,
			    const enum usbd_speed speed)
{
	struct usbd_config_node *cfg_nd =
		(speed == USBD_SPEED_HS) ? &cdc_acm_serial_hs_config
					 : &cdc_acm_serial_fs_config;
	int err;

	err = usbd_add_configuration(uds_ctx, speed, cfg_nd);
	if (err) {
		LOG_ERR("Failed to add configuration (%d)", err);
		return err;
	}

	for (size_t n = 0; n < ARRAY_SIZE(uart_devs); n++) {
		if (!device_is_ready(uart_devs[n])) {
			LOG_ERR("%s not ready", uart_devs[n]->name);
			return -ENODEV;
		}
		err = register_uart_cdc(uds_ctx, speed, uart_devs[n]);
		if (err) {
			return err;
		}
	}

	return usbd_device_set_code_triple(uds_ctx, speed,
					   USB_BCC_MISCELLANEOUS, 0x02, 0x01);
}

static int usb_cdc_triple_init(void)
{
	int err;

	err = usbd_add_descriptor(&cdc_acm_serial, &cdc_acm_serial_lang);
	if (err) {
		return err;
	}
	err = usbd_add_descriptor(&cdc_acm_serial, &cdc_acm_serial_mfr);
	if (err) {
		return err;
	}
	err = usbd_add_descriptor(&cdc_acm_serial, &cdc_acm_serial_product);
	if (err) {
		return err;
	}
	IF_ENABLED(CONFIG_HWINFO, (
		err = usbd_add_descriptor(&cdc_acm_serial, &cdc_acm_serial_sn);
	));
	if (err) {
		return err;
	}

	if (USBD_SUPPORTS_HIGH_SPEED &&
	    usbd_caps_speed(&cdc_acm_serial) == USBD_SPEED_HS) {
		err = register_cdc_acm(&cdc_acm_serial, USBD_SPEED_HS);
		if (err) {
			return err;
		}
	}

	err = register_cdc_acm(&cdc_acm_serial, USBD_SPEED_FS);
	if (err) {
		return err;
	}

	err = usbd_init(&cdc_acm_serial);
	if (err) {
		LOG_ERR("usbd_init failed (%d)", err);
		return err;
	}

	err = usbd_enable(&cdc_acm_serial);
	if (err) {
		LOG_ERR("usbd_enable failed (%d)", err);
		return err;
	}

	printk("USB CDC: shell/FLPR/SMP enabled\n");

	return 0;
}

SYS_INIT(usb_cdc_triple_init, APPLICATION, CONFIG_APPLICATION_INIT_PRIORITY);
