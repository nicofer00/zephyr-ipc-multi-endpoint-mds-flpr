/*
 * Copyright (c) 2026 Nordic Semiconductor ASA
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 * Shell CLI for DK MX25R64 on SPI00. Uses a RAM spi_config so
 * `spiflash speed` can raise the CLI clock up to 32 MHz without
 * mutating the RO DTS spi_dt_spec used by the flash driver / MCUboot.
 */

#include <errno.h>
#include <stdlib.h>
#include <string.h>

#include <zephyr/device.h>
#include <zephyr/devicetree.h>
#include <zephyr/drivers/flash.h>
#include <zephyr/drivers/spi.h>
#include <zephyr/kernel.h>
#include <zephyr/shell/shell.h>
#include <zephyr/sys/byteorder.h>
#include <zephyr/sys/util.h>

#if !DT_NODE_HAS_STATUS(DT_NODELABEL(mx25r64), okay)
#error "mx25r64 must be enabled in the board overlay for spi_flash_cli"
#endif

#define SPI_FLASH_NODE DT_NODELABEL(mx25r64)

/* High offset on MX25 (OTA slot1 is internal RRAM, not this chip). */
#define SPI_FLASH_CLI_OFFSET 0xff000U
#define SPI_FLASH_CLI_SECTOR 4096U

#define SPI_FLASH_FREQ_MIN_HZ 1000000U
#define SPI_FLASH_FREQ_MAX_HZ 32000000U
#define SPI_FLASH_FREQ_DEFAULT_HZ 8000000U

#define SPI_NOR_CMD_WREN 0x06
#define SPI_NOR_CMD_RDSR 0x05
#define SPI_NOR_CMD_RDID 0x9F
#define SPI_NOR_CMD_READ 0x03
#define SPI_NOR_CMD_PP   0x02
#define SPI_NOR_CMD_SE   0x20
#define SPI_NOR_SR_WIP   BIT(0)

#define SPI_NOR_PAGE_SIZE 256U

static const struct spi_dt_spec flash_spi = SPI_DT_SPEC_GET(
	SPI_FLASH_NODE, SPI_OP_MODE_MASTER | SPI_WORD_SET(8) | SPI_TRANSFER_MSB, 0);

static struct spi_config cli_spi_cfg;
static bool cli_spi_ready;

static int cli_spi_init(void)
{
	if (cli_spi_ready) {
		return 0;
	}

	if (!spi_is_ready_dt(&flash_spi)) {
		return -ENODEV;
	}

	cli_spi_cfg = flash_spi.config;
	if (cli_spi_cfg.frequency == 0U) {
		cli_spi_cfg.frequency = SPI_FLASH_FREQ_DEFAULT_HZ;
	}
	cli_spi_ready = true;
	return 0;
}

static int spi_xfer(const struct spi_buf_set *tx, const struct spi_buf_set *rx)
{
	int err = cli_spi_init();

	if (err) {
		return err;
	}

	return spi_transceive(flash_spi.bus, &cli_spi_cfg, tx, rx);
}

static int spi_write_cmd(const uint8_t *cmd, size_t cmd_len)
{
	const struct spi_buf tx_buf = {
		.buf = (void *)cmd,
		.len = cmd_len,
	};
	const struct spi_buf_set tx = {
		.buffers = &tx_buf,
		.count = 1,
	};

	return spi_xfer(&tx, NULL);
}

static int spi_nor_wren(void)
{
	uint8_t cmd = SPI_NOR_CMD_WREN;

	return spi_write_cmd(&cmd, 1);
}

static int spi_nor_wait_ready(const struct shell *sh)
{
	for (int i = 0; i < 5000; i++) {
		uint8_t cmd = SPI_NOR_CMD_RDSR;
		uint8_t sr = 0xff;
		const struct spi_buf tx_bufs[] = {
			{ .buf = &cmd, .len = 1 },
			{ .buf = NULL, .len = 1 },
		};
		const struct spi_buf rx_bufs[] = {
			{ .buf = NULL, .len = 1 },
			{ .buf = &sr, .len = 1 },
		};
		const struct spi_buf_set tx = { .buffers = tx_bufs, .count = 2 };
		const struct spi_buf_set rx = { .buffers = rx_bufs, .count = 2 };
		int err = spi_xfer(&tx, &rx);

		if (err) {
			return err;
		}
		if ((sr & SPI_NOR_SR_WIP) == 0) {
			return 0;
		}
		k_msleep(1);
	}

	shell_error(sh, "flash busy timeout");
	return -ETIMEDOUT;
}

static int spi_nor_rdid(uint8_t id[3])
{
	uint8_t cmd = SPI_NOR_CMD_RDID;
	const struct spi_buf tx_bufs[] = {
		{ .buf = &cmd, .len = 1 },
		{ .buf = NULL, .len = 3 },
	};
	const struct spi_buf rx_bufs[] = {
		{ .buf = NULL, .len = 1 },
		{ .buf = id, .len = 3 },
	};
	const struct spi_buf_set tx = { .buffers = tx_bufs, .count = 2 };
	const struct spi_buf_set rx = { .buffers = rx_bufs, .count = 2 };

	return spi_xfer(&tx, &rx);
}

static int spi_nor_read(uint32_t addr, uint8_t *data, size_t len)
{
	uint8_t cmd[4] = {
		SPI_NOR_CMD_READ,
		(addr >> 16) & 0xff,
		(addr >> 8) & 0xff,
		addr & 0xff,
	};
	const struct spi_buf tx_bufs[] = {
		{ .buf = cmd, .len = sizeof(cmd) },
		{ .buf = NULL, .len = len },
	};
	const struct spi_buf rx_bufs[] = {
		{ .buf = NULL, .len = sizeof(cmd) },
		{ .buf = data, .len = len },
	};
	const struct spi_buf_set tx = { .buffers = tx_bufs, .count = 2 };
	const struct spi_buf_set rx = { .buffers = rx_bufs, .count = 2 };

	return spi_xfer(&tx, &rx);
}

static int spi_nor_erase_sector(const struct shell *sh, uint32_t addr)
{
	uint8_t cmd[4] = {
		SPI_NOR_CMD_SE,
		(addr >> 16) & 0xff,
		(addr >> 8) & 0xff,
		addr & 0xff,
	};
	int err = spi_nor_wren();

	if (err) {
		return err;
	}
	err = spi_write_cmd(cmd, sizeof(cmd));
	if (err) {
		return err;
	}
	return spi_nor_wait_ready(sh);
}

static int spi_nor_page_program(const struct shell *sh, uint32_t addr,
				const uint8_t *data, size_t len)
{
	uint8_t hdr[4] = {
		SPI_NOR_CMD_PP,
		(addr >> 16) & 0xff,
		(addr >> 8) & 0xff,
		addr & 0xff,
	};
	const struct spi_buf tx_bufs[] = {
		{ .buf = hdr, .len = sizeof(hdr) },
		{ .buf = (void *)data, .len = len },
	};
	const struct spi_buf_set tx = { .buffers = tx_bufs, .count = 2 };
	int err = spi_nor_wren();

	if (err) {
		return err;
	}
	err = spi_xfer(&tx, NULL);
	if (err) {
		return err;
	}
	return spi_nor_wait_ready(sh);
}

static int cmd_speed(const struct shell *sh, size_t argc, char **argv)
{
	int err = cli_spi_init();

	if (err) {
		shell_error(sh, "SPI not ready (%d)", err);
		return err;
	}

	if (argc == 1) {
		shell_print(sh, "CLI SPI frequency: %u Hz (DTS flash driver stays at %u Hz)",
			    cli_spi_cfg.frequency,
			    flash_spi.config.frequency ? flash_spi.config.frequency
						       : SPI_FLASH_FREQ_DEFAULT_HZ);
		return 0;
	}

	char *end = NULL;
	unsigned long hz = strtoul(argv[1], &end, 0);

	if (end == argv[1] || *end != '\0') {
		shell_error(sh, "invalid frequency");
		return -EINVAL;
	}
	if (hz < SPI_FLASH_FREQ_MIN_HZ || hz > SPI_FLASH_FREQ_MAX_HZ) {
		shell_error(sh, "frequency must be %u..%u Hz",
			    SPI_FLASH_FREQ_MIN_HZ, SPI_FLASH_FREQ_MAX_HZ);
		return -EINVAL;
	}

	cli_spi_cfg.frequency = (uint32_t)hz;
	shell_print(sh, "CLI SPI frequency set to %u Hz", cli_spi_cfg.frequency);
	shell_print(sh, "Note: >8 MHz may need high-drive GPIOs; validate with spiflash id/test");
	return 0;
}

static int cmd_id(const struct shell *sh, size_t argc, char **argv)
{
	uint8_t id[3];
	int err;

	ARG_UNUSED(argc);
	ARG_UNUSED(argv);

	err = spi_nor_rdid(id);
	if (err) {
		shell_error(sh, "RDID failed (%d) @ %u Hz", err, cli_spi_cfg.frequency);
		return err;
	}

	shell_print(sh, "JEDEC ID: %02x %02x %02x @ %u Hz", id[0], id[1], id[2],
		    cli_spi_cfg.frequency);
	if (id[0] == 0xc2 && id[1] == 0x28 && id[2] == 0x17) {
		shell_print(sh, "matches MX25R64");
	}
	return 0;
}

static int cmd_erase(const struct shell *sh, size_t argc, char **argv)
{
	int err;

	ARG_UNUSED(argc);
	ARG_UNUSED(argv);

	err = spi_nor_erase_sector(sh, SPI_FLASH_CLI_OFFSET);
	if (err) {
		shell_error(sh, "erase failed (%d)", err);
		return err;
	}
	shell_print(sh, "erased 4 KiB @ 0x%08x @ %u Hz", SPI_FLASH_CLI_OFFSET,
		    cli_spi_cfg.frequency);
	return 0;
}

static int cmd_write(const struct shell *sh, size_t argc, char **argv)
{
	static const uint8_t pattern[] = { 0x55, 0xaa, 0x66, 0x99, 0x12, 0x34, 0x56, 0x78 };
	int err;

	ARG_UNUSED(argc);
	ARG_UNUSED(argv);

	err = spi_nor_page_program(sh, SPI_FLASH_CLI_OFFSET, pattern, sizeof(pattern));
	if (err) {
		shell_error(sh, "program failed (%d)", err);
		return err;
	}
	shell_print(sh, "wrote %u bytes @ 0x%08x @ %u Hz", (unsigned)sizeof(pattern),
		    SPI_FLASH_CLI_OFFSET, cli_spi_cfg.frequency);
	return 0;
}

static int cmd_read(const struct shell *sh, size_t argc, char **argv)
{
	uint8_t buf[16];
	int err;

	ARG_UNUSED(argc);
	ARG_UNUSED(argv);

	err = spi_nor_read(SPI_FLASH_CLI_OFFSET, buf, sizeof(buf));
	if (err) {
		shell_error(sh, "read failed (%d)", err);
		return err;
	}

	shell_fprintf(sh, SHELL_NORMAL, "read @ 0x%08x @ %u Hz:", SPI_FLASH_CLI_OFFSET,
		      cli_spi_cfg.frequency);
	for (size_t i = 0; i < sizeof(buf); i++) {
		shell_fprintf(sh, SHELL_NORMAL, " %02x", buf[i]);
	}
	shell_print(sh, "");
	return 0;
}

static int cmd_test(const struct shell *sh, size_t argc, char **argv)
{
	static const uint8_t expected[] = { 0x55, 0xaa, 0x66, 0x99 };
	uint8_t buf[sizeof(expected)];
	int err;

	ARG_UNUSED(argc);
	ARG_UNUSED(argv);

	shell_print(sh, "SPI flash R/W test @ 0x%08x, %u Hz", SPI_FLASH_CLI_OFFSET,
		    cli_spi_cfg.frequency);

	err = spi_nor_erase_sector(sh, SPI_FLASH_CLI_OFFSET);
	if (err) {
		shell_error(sh, "erase failed (%d)", err);
		return err;
	}

	memset(buf, 0, sizeof(buf));
	err = spi_nor_read(SPI_FLASH_CLI_OFFSET, buf, sizeof(buf));
	if (err) {
		shell_error(sh, "read-after-erase failed (%d)", err);
		return err;
	}
	for (size_t i = 0; i < sizeof(buf); i++) {
		if (buf[i] != 0xff) {
			shell_error(sh, "erase verify fail at +%u: %02x", (unsigned)i, buf[i]);
			return -EIO;
		}
	}

	err = spi_nor_page_program(sh, SPI_FLASH_CLI_OFFSET, expected, sizeof(expected));
	if (err) {
		shell_error(sh, "program failed (%d)", err);
		return err;
	}

	memset(buf, 0, sizeof(buf));
	err = spi_nor_read(SPI_FLASH_CLI_OFFSET, buf, sizeof(buf));
	if (err) {
		shell_error(sh, "read-after-write failed (%d)", err);
		return err;
	}
	if (memcmp(expected, buf, sizeof(expected)) != 0) {
		shell_error(sh, "data mismatch");
		return -EIO;
	}

	shell_print(sh, "pass");
	return 0;
}

static int cmd_info(const struct shell *sh, size_t argc, char **argv)
{
	const struct device *flash_dev = DEVICE_DT_GET(SPI_FLASH_NODE);

	ARG_UNUSED(argc);
	ARG_UNUSED(argv);

	(void)cli_spi_init();

	shell_print(sh, "device: %s ready=%d", flash_dev->name, device_is_ready(flash_dev));
	shell_print(sh, "CLI offset: 0x%08x (sector %u)", SPI_FLASH_CLI_OFFSET,
		    SPI_FLASH_CLI_SECTOR);
	shell_print(sh, "CLI freq: %u Hz (max %u)",
		    cli_spi_ready ? cli_spi_cfg.frequency : 0U, SPI_FLASH_FREQ_MAX_HZ);
	shell_print(sh, "DTS spi-max-frequency: %u Hz",
		    flash_spi.config.frequency ? flash_spi.config.frequency
					       : SPI_FLASH_FREQ_DEFAULT_HZ);
	return 0;
}

SHELL_STATIC_SUBCMD_SET_CREATE(
	spiflash_cmds,
	SHELL_CMD(info, NULL, "Show SPI flash CLI info", cmd_info),
	SHELL_CMD(id, NULL, "Read JEDEC ID at CLI SPI speed", cmd_id),
	SHELL_CMD_ARG(speed, NULL, "Get/set CLI SPI Hz (1e6..32e6)", cmd_speed, 1, 1),
	SHELL_CMD(erase, NULL, "Erase CLI test sector", cmd_erase),
	SHELL_CMD(write, NULL, "Program test pattern in CLI sector", cmd_write),
	SHELL_CMD(read, NULL, "Read 16 bytes from CLI sector", cmd_read),
	SHELL_CMD(test, NULL, "Erase/write/read verify at CLI speed", cmd_test),
	SHELL_SUBCMD_SET_END);

SHELL_CMD_REGISTER(spiflash, &spiflash_cmds, "SPI00 MX25R64 flash CLI", NULL);
