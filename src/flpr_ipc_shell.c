#include "flpr_ipc.h"

#include <stdlib.h>
#include <string.h>

#include <zephyr/shell/shell.h>

static int cmd_status(const struct shell *sh, size_t argc, char **argv)
{
	ARG_UNUSED(argc);
	ARG_UNUSED(argv);

	shell_print(sh, "flpr_log  bound: %s", flpr_ipc_log_bound() ? "yes" : "no");
	shell_print(sh, "flpr_ctrl bound: %s", flpr_ipc_ctrl_bound() ? "yes" : "no");
	shell_print(sh, "echo: %s", flpr_ipc_echo_enabled() ? "on" : "off");
	return 0;
}

static int cmd_ping(const struct shell *sh, size_t argc, char **argv)
{
	int ret;

	ARG_UNUSED(argc);
	ARG_UNUSED(argv);

	ret = flpr_ipc_send_ping();
	if (ret < 0) {
		shell_error(sh, "ping failed (%d)", ret);
		return ret;
	}

	shell_print(sh, "pong ok");
	return 0;
}

static int cmd_stats(const struct shell *sh, size_t argc, char **argv)
{
	int ret;

	ARG_UNUSED(argc);
	ARG_UNUSED(argv);

	ret = flpr_ipc_request_stats();
	if (ret < 0) {
		shell_error(sh, "stats request failed (%d)", ret);
		return ret;
	}

	shell_print(sh, "stats requested");
	return 0;
}

static int cmd_log(const struct shell *sh, size_t argc, char **argv)
{
	int ret;

	if (argc < 2) {
		shell_error(sh, "usage: flpr log <text>");
		return -EINVAL;
	}

	ret = flpr_ipc_request_log(argv[1]);
	if (ret < 0) {
		shell_error(sh, "log request failed (%d)", ret);
		return ret;
	}

	shell_print(sh, "log requested");
	return 0;
}

static int cmd_echo(const struct shell *sh, size_t argc, char **argv)
{
	if (argc < 2) {
		shell_print(sh, "echo is %s", flpr_ipc_echo_enabled() ? "on" : "off");
		return 0;
	}

	if (strcmp(argv[1], "on") == 0) {
		flpr_ipc_set_echo(true);
	} else if (strcmp(argv[1], "off") == 0) {
		flpr_ipc_set_echo(false);
	} else {
		shell_error(sh, "usage: flpr echo on|off");
		return -EINVAL;
	}

	shell_print(sh, "echo %s", argv[1]);
	return 0;
}

SHELL_STATIC_SUBCMD_SET_CREATE(flpr_cmds,
	SHELL_CMD(status, NULL, "Show FLPR IPC bind state", cmd_status),
	SHELL_CMD(ping, NULL, "Ping FLPR over flpr_ctrl", cmd_ping),
	SHELL_CMD(stats, NULL, "Request FLPR stats", cmd_stats),
	SHELL_CMD_ARG(log, NULL, "Request FLPR log line", cmd_log, 2, 0),
	SHELL_CMD_ARG(echo, NULL, "Echo FLPR RX to console: on|off", cmd_echo, 1, 1),
	SHELL_SUBCMD_SET_END
);

SHELL_CMD_REGISTER(flpr, &flpr_cmds, "FLPR IPC test commands", NULL);
