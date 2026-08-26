#include "flpr_ipc_proto.h"

#include <zephyr/ipc/ipc_service.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log_backend.h>
#include <zephyr/logging/log_backend_std.h>
#include <zephyr/logging/log_ctrl.h>
#include <zephyr/logging/log_output.h>

extern int flpr_remote_send_log(const char *text, size_t len);

static uint8_t log_buf[FLPR_LOG_MAX_LEN];

static int char_out(uint8_t *data, size_t length, void *ctx)
{
	ARG_UNUSED(ctx);

	if (length == 0) {
		return length;
	}

	/* Drop trailing newlines for cleaner IPC framing. */
	while (length > 0 && (data[length - 1] == '\n' || data[length - 1] == '\r')) {
		length--;
	}

	if (length == 0) {
		return 0;
	}

	if (length > FLPR_LOG_MAX_LEN) {
		length = FLPR_LOG_MAX_LEN;
	}

	(void)flpr_remote_send_log((const char *)data, length);
	return (int)length;
}

LOG_OUTPUT_DEFINE(flpr_log_output, char_out, log_buf, sizeof(log_buf));

static void panic(struct log_backend const *const backend)
{
	ARG_UNUSED(backend);
}

static void dropped(const struct log_backend *const backend, uint32_t cnt)
{
	ARG_UNUSED(backend);
	ARG_UNUSED(cnt);
}

static void process(const struct log_backend *const backend, union log_msg_generic *msg)
{
	ARG_UNUSED(backend);

	log_format_func_t func = log_format_func_t_get(LOG_OUTPUT_TEXT);

	func(&flpr_log_output, &msg->log, log_backend_std_get_flags());
}

static int format_set(const struct log_backend *const backend, uint32_t log_type)
{
	ARG_UNUSED(backend);
	ARG_UNUSED(log_type);
	return 0;
}

static const struct log_backend_api flpr_log_api = {
	.process = process,
	.panic = panic,
	.dropped = dropped,
	.format_set = format_set,
};

LOG_BACKEND_DEFINE(flpr_ipc_log_backend, flpr_log_api, true);
