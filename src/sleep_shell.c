/*
 * Copyright (c) 2026 Nordic Semiconductor ASA
 * SPDX-License-Identifier: Apache-2.0
 *
 * Shell commands to exercise nRF54LM20 System ON idle (CDC/button wake)
 * and System OFF (GPIO DETECT / GRTC wake → reset).
 */

#include "sleep_shell.h"
#include "usb_cdc_triple.h"

#include <stdlib.h>
#include <string.h>

#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/bluetooth/hci.h>
#include <zephyr/device.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/drivers/hwinfo.h>
#include <zephyr/drivers/timer/nrf_grtc_timer.h>
#include <zephyr/kernel.h>
#include <zephyr/pm/device.h>
#include <zephyr/shell/shell.h>
#include <zephyr/sys/atomic.h>
#include <zephyr/sys/poweroff.h>
#include <zephyr/sys/printk.h>
#include <zephyr/sys/util.h>

#include <hal/nrf_power.h>

#include <dk_buttons_and_leds.h>

enum sleep_wake_src {
	SLEEP_WAKE_NONE = 0,
	SLEEP_WAKE_UART,
	SLEEP_WAKE_GPIO,
};

static atomic_t quiesced;
static enum sleep_wake_src wake_src;
static const struct shell *idle_sh;
static const struct gpio_dt_spec sw0 = GPIO_DT_SPEC_GET(DT_ALIAS(sw0), gpios);

bool sleep_shell_is_quiesced(void)
{
	return atomic_get(&quiesced) != 0;
}

static void set_quiesced(bool on)
{
	atomic_set(&quiesced, on ? 1 : 0);
}

static void idle_finish(const struct shell *sh)
{
	/* Only one wake path may complete (CDC bypass vs Button0). */
	if (!atomic_cas(&quiesced, 1, 0)) {
		return;
	}

	shell_set_bypass(sh, NULL, NULL);
	idle_sh = NULL;

	if (wake_src == SLEEP_WAKE_UART) {
		shell_print(sh, "Woke from System ON idle (source: uart)");
	} else if (wake_src == SLEEP_WAKE_GPIO) {
		shell_print(sh, "Woke from System ON idle (source: gpio)");
	} else {
		shell_print(sh, "Woke from System ON idle (source: unknown)");
	}
}

void sleep_shell_button_notify(uint32_t buttons)
{
	if (!sleep_shell_is_quiesced()) {
		return;
	}

	if (buttons & DK_BTN1_MSK) {
		const struct shell *sh = idle_sh;

		wake_src = SLEEP_WAKE_GPIO;
		if (sh != NULL) {
			idle_finish(sh);
		}
	}
}

void sleep_shell_print_reset_cause(void)
{
	uint32_t cause = 0;
	uint32_t supported = 0;
	int err;

	err = hwinfo_get_reset_cause(&cause);
	if (err) {
		printk("Reset cause: unavailable (%d)\n", err);
		return;
	}

	err = hwinfo_get_supported_reset_cause(&supported);
	if (err) {
		printk("Reset cause: 0x%08x (supported query failed %d)\n", cause, err);
		return;
	}

	printk("Reset cause: 0x%08x", cause);
	if (cause & supported & RESET_DEBUG) {
		printk(" (debugger)");
	} else if (cause & supported & RESET_CLOCK) {
		printk(" (System OFF wake: GRTC)");
	} else if (cause & supported & RESET_LOW_POWER_WAKE) {
		printk(" (System OFF wake: GPIO)");
	} else if (cause & supported & RESET_PIN) {
		printk(" (pin reset)");
	} else if (cause & supported & RESET_POR) {
		printk(" (power-on)");
	} else if (cause & supported & RESET_SOFTWARE) {
		printk(" (software)");
	} else if (cause == 0U) {
		printk(" (none / cleared)");
	} else {
		printk(" (other)");
	}
	printk("\n");
}

static void disconnect_conn(struct bt_conn *conn, void *data)
{
	ARG_UNUSED(data);
	(void)bt_conn_disconnect(conn, BT_HCI_ERR_REMOTE_USER_TERM_CONN);
}

static int stop_ble_activity(const struct shell *sh)
{
	int err;

	bt_conn_foreach(BT_CONN_TYPE_LE, disconnect_conn, NULL);

	err = bt_le_adv_stop();
	if (err && err != -EALREADY) {
		shell_warn(sh, "bt_le_adv_stop: %d", err);
		return err;
	}

	return 0;
}

static void idle_bypass_cb(const struct shell *sh, uint8_t *data, size_t len, void *user_data)
{
	ARG_UNUSED(user_data);

	if (len > 0U && data != NULL) {
		wake_src = SLEEP_WAKE_UART;
		idle_finish(sh);
	}
}

static int configure_sw0_sense(const struct shell *sh)
{
	int rc;

	if (!gpio_is_ready_dt(&sw0)) {
		shell_error(sh, "sw0 GPIO not ready");
		return -ENODEV;
	}

	rc = gpio_pin_configure_dt(&sw0, GPIO_INPUT);
	if (rc < 0) {
		shell_error(sh, "sw0 configure failed (%d)", rc);
		return rc;
	}

	/* PORT sense / LEVEL_ACTIVE — required for System OFF DETECT wake. */
	rc = gpio_pin_interrupt_configure_dt(&sw0, GPIO_INT_LEVEL_ACTIVE);
	if (rc < 0) {
		shell_error(sh, "sw0 sense interrupt failed (%d)", rc);
		return rc;
	}

	return 0;
}

static int enter_system_off(const struct shell *sh, int grtc_sec)
{
	int rc;

	shell_print(sh, "Preparing System OFF...");
	(void)stop_ble_activity(sh);

	rc = configure_sw0_sense(sh);
	if (rc) {
		return rc;
	}

	if (grtc_sec > 0) {
		rc = z_nrf_grtc_wakeup_prepare((uint64_t)grtc_sec * USEC_PER_SEC);
		if (rc < 0) {
			shell_error(sh, "GRTC wakeup prepare failed (%d)", rc);
			return rc;
		}
		shell_print(sh, "GRTC wake in %d s (also Button0)", grtc_sec);
	} else {
		shell_print(sh, "Press Button0 (sw0) to wake (device will reset)");
	}

	shell_print(sh, "Disabling USB CDC; shell drops until reboot");
	k_msleep(50);

	rc = usb_cdc_triple_disable();
	if (rc) {
		shell_warn(sh, "USB disable failed (%d); continuing", rc);
	}

#if DT_HAS_CHOSEN(zephyr_console)
	{
		const struct device *cons = DEVICE_DT_GET(DT_CHOSEN(zephyr_console));

		if (device_is_ready(cons)) {
			rc = pm_device_action_run(cons, PM_DEVICE_ACTION_SUSPEND);
			if (rc < 0 && rc != -ENOTSUP && rc != -EALREADY) {
				printk("console suspend failed (%d)\n", rc);
			}
		}
	}
#endif

	hwinfo_clear_reset_cause();
	sys_poweroff();

	/* Not reached on real System OFF. */
	return -EIO;
}

static int cmd_status(const struct shell *sh, size_t argc, char **argv)
{
	ARG_UNUSED(argc);
	ARG_UNUSED(argv);

	sleep_shell_print_reset_cause();

#if NRF_POWER_HAS_CONST_LATENCY && defined(POWER_CONSTLATSTAT_STATUS_Msk)
	shell_print(sh, "CONSTLAT: %s",
		    (NRF_POWER->CONSTLATSTAT & POWER_CONSTLATSTAT_STATUS_Msk) ?
			    "enabled" :
			    "disabled (LOWPWR default when idle)");
#else
	shell_print(sh, "CONSTLAT status register not available in this HAL build");
#endif

	shell_print(sh, "Wake tips:");
	shell_print(sh, "  sleep idle     — System ON idle; wake on CDC key or Button0");
	shell_print(sh, "  sleep off      — System OFF; wake on Button0 (reset)");
	shell_print(sh, "  sleep off grtc <s> — System OFF; GRTC and/or Button0");
	shell_print(sh, "CDC typing cannot wake System OFF (USB is off).");
	shell_print(sh, "J-Link attached: System OFF may be emulated.");

	return 0;
}

static int cmd_idle(const struct shell *sh, size_t argc, char **argv)
{
	ARG_UNUSED(argc);
	ARG_UNUSED(argv);

	/*
	 * Do not block the shell thread. Bypass RX is handled by shell_process()
	 * on that same thread (see Zephyr shell_thread / state_collect). Blocking
	 * here deadlocks CDC wake; match the shell "devmem load" pattern: arm
	 * bypass and return so the shell thread can drain RX.
	 */
	if (sleep_shell_is_quiesced()) {
		shell_warn(sh, "Already in System ON idle");
		return -EALREADY;
	}

	wake_src = SLEEP_WAKE_NONE;
	idle_sh = sh;
	set_quiesced(true);
	(void)stop_ble_activity(sh);

	shell_print(sh, "System ON idle: type a character or press Button0");
	shell_print(sh, "(BLE advertising stopped; use 'bt advertise on' after wake if needed)");
	shell_set_bypass(sh, idle_bypass_cb, NULL);

	return 0;
}

static int cmd_off(const struct shell *sh, size_t argc, char **argv)
{
	int grtc_sec = 0;

	if (argc >= 2) {
		if (strcmp(argv[1], "grtc") != 0 || argc < 3) {
			shell_error(sh, "usage: sleep off [grtc <seconds>]");
			return -EINVAL;
		}
		grtc_sec = (int)strtol(argv[2], NULL, 10);
		if (grtc_sec <= 0) {
			shell_error(sh, "grtc seconds must be > 0");
			return -EINVAL;
		}
	}

	set_quiesced(true);
	return enter_system_off(sh, grtc_sec);
}

static int cmd_constlat(const struct shell *sh, size_t argc, char **argv)
{
	ARG_UNUSED(argc);
	ARG_UNUSED(argv);

#if NRF_POWER_HAS_CONST_LATENCY
	nrf_power_task_trigger(NRF_POWER, NRF_POWER_TASK_CONSTLAT);
	shell_print(sh, "CONSTLAT enabled (higher idle current, fixed wake latency)");
	return 0;
#else
	shell_error(sh, "CONSTLAT not supported on this SoC build");
	return -ENOTSUP;
#endif
}

static int cmd_lowpwr(const struct shell *sh, size_t argc, char **argv)
{
	ARG_UNUSED(argc);
	ARG_UNUSED(argv);

#if NRF_POWER_HAS_LOW_POWER
	nrf_power_task_trigger(NRF_POWER, NRF_POWER_TASK_LOWPWR);
	shell_print(sh, "LOWPWR enabled (default System ON idle submode)");
	return 0;
#else
	shell_error(sh, "LOWPWR task not supported on this SoC build");
	return -ENOTSUP;
#endif
}

SHELL_STATIC_SUBCMD_SET_CREATE(sleep_cmds,
	SHELL_CMD(status, NULL, "Reset cause, CONSTLAT, wake tips", cmd_status),
	SHELL_CMD(idle, NULL, "System ON idle; wake on CDC or Button0", cmd_idle),
	SHELL_CMD_ARG(off, NULL, "System OFF [grtc <seconds>]", cmd_off, 1, 2),
	SHELL_CMD(constlat, NULL, "Enable constant-latency idle submode", cmd_constlat),
	SHELL_CMD(lowpwr, NULL, "Enable low-power idle submode", cmd_lowpwr),
	SHELL_SUBCMD_SET_END
);

SHELL_CMD_REGISTER(sleep, &sleep_cmds, "Sleep / System OFF test commands", NULL);
