.. zephyr:code-sample:: ipc_multi_endpoint
   :name: IPC service: Multi-endpoint MDS + FLPR
   :relevant-api: ipc

   Bluetooth MDS on cpuapp with FLPR IPC endpoints and dual USB CDC.

Overview
********

This application combines:

* Bluetooth Peripheral Memfault Diagnostic Service (MDS) on the application core
* A slim FLPR remote using one ``ipc0`` ``icmsg_me`` instance with two endpoints:

  * ``flpr_log`` – FLPR log stream (forwarded to USB CDC ACM 1)
  * ``flpr_ctrl`` – ping/stats/control (including Memfault metrics from FLPR)

USB CDC ACM 0 is the application **shell**. App ``printk``/LOG go to debugger
``uart20``. FLPR logs are forwarded over IPC to USB CDC ACM 1. FLPR local
``printk`` remains on debugger ``uart30``.

Building for nrf54lm20dk/nrf54lm20b/cpuapp
*****************************************

.. zephyr-app-commands::
   :zephyr-app: samples/subsys/ipc/ipc_service/multi_endpoint
   :board: nrf54lm20dk/nrf54lm20b/cpuapp
   :goals: build
   :west-args: --sysbuild
   :gen-args: -Dmulti_endpoint_SNIPPET=nordic-flpr

Shell commands
**************

On the application shell (USB CDC0):

* ``flpr status`` – endpoint bind state
* ``flpr ping`` – round-trip over ``flpr_ctrl``
* ``flpr stats`` – request FLPR stats (updates Memfault metrics)
* ``flpr log <text>`` – ask FLPR to emit a log line on ``flpr_log``
* ``flpr echo on|off`` – also print FLPR RX payloads on the shell (uart20 console)

Memfault shell commands (``mflt ...``) remain available. Set a real
``CONFIG_MEMFAULT_NCS_PROJECT_KEY`` before expecting cloud upload; the sample
ships with ``dummy-key``.

Bonding / CCC persistence uses Settings over ZMS (``CONFIG_SETTINGS_ZMS``).

Expected ports
**************

* Debugger uart20 – app ``printk`` / LOG / MDS boot messages
* USB CDC0 – app shell (``flpr``, ``mflt``, ...)
* USB CDC1 – forwarded FLPR logs (heartbeats, ``flpr log``, ...)
* Debugger uart30 – FLPR local ``printk``
