#ifndef FLPR_IPC_PROTO_H_
#define FLPR_IPC_PROTO_H_

#include <stdint.h>

#define FLPR_IPC_EPT_LOG_NAME  "flpr_log"
#define FLPR_IPC_EPT_CTRL_NAME "flpr_ctrl"

#define FLPR_CTRL_OPCODE_PING     0x01
#define FLPR_CTRL_OPCODE_PONG     0x02
#define FLPR_CTRL_OPCODE_STATS_REQ 0x03
#define FLPR_CTRL_OPCODE_STATS    0x04
#define FLPR_CTRL_OPCODE_LOG_REQ  0x05

struct flpr_ctrl_hdr {
	uint8_t opcode;
	uint8_t seq;
	uint16_t len;
} __packed;

struct flpr_stats {
	uint32_t uptime_ms;
	uint32_t heartbeat;
	uint32_t free_stack;
} __packed;

#define FLPR_LOG_MAX_LEN 120

#endif /* FLPR_IPC_PROTO_H_ */
