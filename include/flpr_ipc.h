#ifndef FLPR_IPC_H_
#define FLPR_IPC_H_

#include <stdbool.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

int flpr_ipc_init(void);

bool flpr_ipc_log_bound(void);
bool flpr_ipc_ctrl_bound(void);

int flpr_ipc_send_ping(void);
int flpr_ipc_request_stats(void);
int flpr_ipc_request_log(const char *text);

void flpr_ipc_set_echo(bool enable);
bool flpr_ipc_echo_enabled(void);

#ifdef __cplusplus
}
#endif

#endif /* FLPR_IPC_H_ */
