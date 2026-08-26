#include "flpr_ipc.h"
#include "flpr_ipc_proto.h"

#include <string.h>

#include <zephyr/device.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/ipc/ipc_service.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/sys/printk.h>

#include <memfault/metrics/metrics.h>

LOG_MODULE_REGISTER(flpr_ipc, CONFIG_LOG_DEFAULT_LEVEL);

static const struct device *ipc0_dev;
static struct ipc_ept ept_log;
static struct ipc_ept ept_ctrl;

static K_SEM_DEFINE(log_bound_sem, 0, 1);
static K_SEM_DEFINE(ctrl_bound_sem, 0, 1);
static K_SEM_DEFINE(pong_sem, 0, 1);

static volatile bool log_bound;
static volatile bool ctrl_bound;
static volatile bool echo_enabled;
static volatile uint8_t last_pong_seq;

static const struct device *cdc1_dev;

static void maybe_write_cdc1(const void *data, size_t len)
{
	if (cdc1_dev == NULL || !device_is_ready(cdc1_dev)) {
		return;
	}

	const uint8_t *p = data;

	for (size_t i = 0; i < len; i++) {
		uart_poll_out(cdc1_dev, p[i]);
	}
}

static void ept_log_bound(void *priv)
{
	ARG_UNUSED(priv);
	log_bound = true;
	k_sem_give(&log_bound_sem);
	printk("FLPR IPC: flpr_log bound\n");
}

static void ept_ctrl_bound(void *priv)
{
	ARG_UNUSED(priv);
	ctrl_bound = true;
	k_sem_give(&ctrl_bound_sem);
	printk("FLPR IPC: flpr_ctrl bound\n");
}

static void ept_log_recv(const void *data, size_t len, void *priv)
{
	ARG_UNUSED(priv);

	if (len == 0 || data == NULL) {
		return;
	}

	maybe_write_cdc1(data, len);
	/* Windows CDC hosts expect CRLF; LF-only leaves the cursor mid-line. */
	maybe_write_cdc1("\r\n", 2);

	if (echo_enabled) {
		printk("[FLPR LOG] %.*s\n", (int)len, (const char *)data);
	}
}

static void handle_stats(const struct flpr_stats *stats)
{
	int err;

	err = MEMFAULT_METRIC_SET_UNSIGNED(flpr_uptime_ms, stats->uptime_ms);
	if (err) {
		LOG_WRN("flpr_uptime_ms set failed: %d", err);
	}

	err = MEMFAULT_METRIC_SET_UNSIGNED(flpr_heartbeat, stats->heartbeat);
	if (err) {
		LOG_WRN("flpr_heartbeat set failed: %d", err);
	}

	err = MEMFAULT_METRIC_SET_UNSIGNED(flpr_free_stack, stats->free_stack);
	if (err) {
		LOG_WRN("flpr_free_stack set failed: %d", err);
	}

	if (echo_enabled) {
		printk("[FLPR STATS] uptime=%u hb=%u stack=%u\n", stats->uptime_ms,
		       stats->heartbeat, stats->free_stack);
	}
}

static void ept_ctrl_recv(const void *data, size_t len, void *priv)
{
	ARG_UNUSED(priv);

	if (data == NULL || len < sizeof(struct flpr_ctrl_hdr)) {
		return;
	}

	const struct flpr_ctrl_hdr *hdr = data;
	const uint8_t *payload = (const uint8_t *)data + sizeof(*hdr);
	size_t payload_len = len - sizeof(*hdr);

	if (hdr->len > payload_len) {
		return;
	}

	switch (hdr->opcode) {
	case FLPR_CTRL_OPCODE_PONG:
		last_pong_seq = hdr->seq;
		k_sem_give(&pong_sem);
		if (echo_enabled) {
			printk("[FLPR CTRL] pong seq=%u\n", hdr->seq);
		}
		break;
	case FLPR_CTRL_OPCODE_STATS:
		if (hdr->len >= sizeof(struct flpr_stats)) {
			struct flpr_stats stats;

			memcpy(&stats, payload, sizeof(stats));
			handle_stats(&stats);
		}
		break;
	default:
		if (echo_enabled) {
			printk("[FLPR CTRL] opcode=0x%02x len=%u\n", hdr->opcode, hdr->len);
		}
		break;
	}
}

static struct ipc_ept_cfg ept_log_cfg = {
	.name = FLPR_IPC_EPT_LOG_NAME,
	.cb = {
		.bound = ept_log_bound,
		.received = ept_log_recv,
	},
};

static struct ipc_ept_cfg ept_ctrl_cfg = {
	.name = FLPR_IPC_EPT_CTRL_NAME,
	.cb = {
		.bound = ept_ctrl_bound,
		.received = ept_ctrl_recv,
	},
};

static int send_ctrl(uint8_t opcode, uint8_t seq, const void *payload, uint16_t payload_len)
{
	uint8_t buf[sizeof(struct flpr_ctrl_hdr) + FLPR_LOG_MAX_LEN];
	struct flpr_ctrl_hdr *hdr = (struct flpr_ctrl_hdr *)buf;

	if (!ctrl_bound) {
		return -ENOTCONN;
	}

	if (payload_len > FLPR_LOG_MAX_LEN) {
		return -EINVAL;
	}

	hdr->opcode = opcode;
	hdr->seq = seq;
	hdr->len = payload_len;

	if (payload_len && payload != NULL) {
		memcpy(buf + sizeof(*hdr), payload, payload_len);
	}

	return ipc_service_send(&ept_ctrl, buf, sizeof(*hdr) + payload_len);
}

int flpr_ipc_init(void)
{
	int ret;

#if DT_NODE_EXISTS(DT_NODELABEL(cdc_acm_uart1))
	cdc1_dev = DEVICE_DT_GET(DT_NODELABEL(cdc_acm_uart1));
#endif

	ipc0_dev = DEVICE_DT_GET(DT_NODELABEL(ipc0));
	if (!device_is_ready(ipc0_dev)) {
		printk("FLPR IPC: ipc0 not ready\n");
		return -ENODEV;
	}

	ret = ipc_service_open_instance(ipc0_dev);
	if (ret < 0 && ret != -EALREADY) {
		printk("FLPR IPC: open failed (%d)\n", ret);
		return ret;
	}

	ret = ipc_service_register_endpoint(ipc0_dev, &ept_log, &ept_log_cfg);
	if (ret < 0) {
		printk("FLPR IPC: register flpr_log failed (%d)\n", ret);
		return ret;
	}

	ret = ipc_service_register_endpoint(ipc0_dev, &ept_ctrl, &ept_ctrl_cfg);
	if (ret < 0) {
		printk("FLPR IPC: register flpr_ctrl failed (%d)\n", ret);
		return ret;
	}

	/* Do not block forever; FLPR may bind slightly later. */
	(void)k_sem_take(&log_bound_sem, K_MSEC(3000));
	(void)k_sem_take(&ctrl_bound_sem, K_MSEC(3000));

	printk("FLPR IPC: init done (log_bound=%d ctrl_bound=%d)\n", log_bound, ctrl_bound);
	return 0;
}

bool flpr_ipc_log_bound(void)
{
	return log_bound;
}

bool flpr_ipc_ctrl_bound(void)
{
	return ctrl_bound;
}

int flpr_ipc_send_ping(void)
{
	static uint8_t seq;
	int ret;

	k_sem_reset(&pong_sem);
	seq++;
	ret = send_ctrl(FLPR_CTRL_OPCODE_PING, seq, NULL, 0);
	if (ret < 0) {
		return ret;
	}

	ret = k_sem_take(&pong_sem, K_MSEC(1000));
	if (ret < 0) {
		return ret;
	}

	if (last_pong_seq != seq) {
		return -EIO;
	}

	return 0;
}

int flpr_ipc_request_stats(void)
{
	return send_ctrl(FLPR_CTRL_OPCODE_STATS_REQ, 0, NULL, 0);
}

int flpr_ipc_request_log(const char *text)
{
	size_t len;

	if (text == NULL) {
		return -EINVAL;
	}

	len = strlen(text);
	if (len == 0) {
		return -EINVAL;
	}
	if (len > FLPR_LOG_MAX_LEN) {
		len = FLPR_LOG_MAX_LEN;
	}

	return send_ctrl(FLPR_CTRL_OPCODE_LOG_REQ, 0, text, (uint16_t)len);
}

void flpr_ipc_set_echo(bool enable)
{
	echo_enabled = enable;
}

bool flpr_ipc_echo_enabled(void)
{
	return echo_enabled;
}
