#include "flpr_ipc_proto.h"

#include <string.h>

#include <zephyr/device.h>
#include <zephyr/ipc/ipc_service.h>
#include <zephyr/kernel.h>
#include <zephyr/sys/printk.h>

#define STACKSIZE 2048
#define PRIORITY  K_PRIO_PREEMPT(2)

static const struct device *ipc0_dev;
static struct ipc_ept ept_log;
static struct ipc_ept ept_ctrl;

static K_SEM_DEFINE(log_bound_sem, 0, 1);
static K_SEM_DEFINE(ctrl_bound_sem, 0, 1);

static volatile bool log_bound;
static volatile bool ctrl_bound;
static uint32_t heartbeat;

int flpr_remote_send_log(const char *text, size_t len)
{
	if (!log_bound || text == NULL || len == 0) {
		return -ENOTCONN;
	}

	if (len > FLPR_LOG_MAX_LEN) {
		len = FLPR_LOG_MAX_LEN;
	}

	return ipc_service_send(&ept_log, text, len);
}

static void ept_log_bound(void *priv)
{
	ARG_UNUSED(priv);
	log_bound = true;
	k_sem_give(&log_bound_sem);
	printk("REMOTE: flpr_log bound\n");
}

static void ept_ctrl_bound(void *priv)
{
	ARG_UNUSED(priv);
	ctrl_bound = true;
	k_sem_give(&ctrl_bound_sem);
	printk("REMOTE: flpr_ctrl bound\n");
}

static int send_ctrl(uint8_t opcode, uint8_t seq, const void *payload, uint16_t payload_len)
{
	uint8_t buf[sizeof(struct flpr_ctrl_hdr) + sizeof(struct flpr_stats) + FLPR_LOG_MAX_LEN];
	struct flpr_ctrl_hdr *hdr = (struct flpr_ctrl_hdr *)buf;

	if (!ctrl_bound) {
		return -ENOTCONN;
	}
	if (payload_len > (sizeof(buf) - sizeof(*hdr))) {
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

static int send_log_line(const char *text)
{
	return flpr_remote_send_log(text, text ? strlen(text) : 0);
}

static void send_stats(void)
{
	struct flpr_stats stats = {
		.uptime_ms = k_uptime_get_32(),
		.heartbeat = ++heartbeat,
		.free_stack = 0,
	};

	(void)send_ctrl(FLPR_CTRL_OPCODE_STATS, 0, &stats, sizeof(stats));
}

static void ept_log_recv(const void *data, size_t len, void *priv)
{
	ARG_UNUSED(data);
	ARG_UNUSED(len);
	ARG_UNUSED(priv);
}

static void ept_ctrl_recv(const void *data, size_t len, void *priv)
{
	ARG_UNUSED(priv);

	if (data == NULL || len < sizeof(struct flpr_ctrl_hdr)) {
		return;
	}

	const struct flpr_ctrl_hdr *hdr = data;
	const uint8_t *payload = (const uint8_t *)data + sizeof(*hdr);

	switch (hdr->opcode) {
	case FLPR_CTRL_OPCODE_PING:
		(void)send_ctrl(FLPR_CTRL_OPCODE_PONG, hdr->seq, NULL, 0);
		break;
	case FLPR_CTRL_OPCODE_STATS_REQ:
		send_stats();
		break;
	case FLPR_CTRL_OPCODE_LOG_REQ: {
		char line[FLPR_LOG_MAX_LEN + 1];
		uint16_t n = hdr->len;

		if (n > FLPR_LOG_MAX_LEN) {
			n = FLPR_LOG_MAX_LEN;
		}
		if (n > 0) {
			memcpy(line, payload, n);
		}
		line[n] = '\0';
		printk("REMOTE log-req: %s\n", line);
		(void)send_log_line(line);
		break;
	}
	default:
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

static void heartbeat_entry(void *a, void *b, void *c)
{
	ARG_UNUSED(a);
	ARG_UNUSED(b);
	ARG_UNUSED(c);

	while (1) {
		if (ctrl_bound) {
			send_stats();
		}
		if (log_bound) {
			char msg[48];

			snprintk(msg, sizeof(msg), "FLPR heartbeat %u", heartbeat);
			(void)send_log_line(msg);
			printk("%s\n", msg);
		}
		k_sleep(K_SECONDS(5));
	}
}

K_THREAD_DEFINE(hb_tid, STACKSIZE, heartbeat_entry, NULL, NULL, NULL, PRIORITY, 0, 0);

int main(void)
{
	int ret;

	printk("FLPR remote IPC stub starting\n");

	ipc0_dev = DEVICE_DT_GET(DT_NODELABEL(ipc0));
	if (!device_is_ready(ipc0_dev)) {
		printk("ipc0 not ready\n");
		return 0;
	}

	ret = ipc_service_open_instance(ipc0_dev);
	if (ret < 0 && ret != -EALREADY) {
		printk("open_instance failed (%d)\n", ret);
		return 0;
	}

	ret = ipc_service_register_endpoint(ipc0_dev, &ept_log, &ept_log_cfg);
	if (ret < 0) {
		printk("register flpr_log failed (%d)\n", ret);
		return 0;
	}

	ret = ipc_service_register_endpoint(ipc0_dev, &ept_ctrl, &ept_ctrl_cfg);
	if (ret < 0) {
		printk("register flpr_ctrl failed (%d)\n", ret);
		return 0;
	}

	(void)k_sem_take(&log_bound_sem, K_FOREVER);
	(void)k_sem_take(&ctrl_bound_sem, K_FOREVER);
	printk("FLPR endpoints bound\n");

	return 0;
}
