# FreeRTOS dependency

Source: STM32Cube FW_F1 V1.8.7, FreeRTOS kernel V10.3.1.

This directory contains the kernel C sources and headers, ST's CMSIS-RTOS V2
adapter, the Keil ARMCC Cortex-M3 port (`portable/RVDS/ARM_CM3`), and `heap_4.c`.
Unused processor ports and heap implementations are omitted. Sources are copied
without functional changes; upstream copyright notices are retained.

Kernel licensing is in `License/license.txt`. The CMSIS adapter files retain
their Apache-2.0 licensing notices. Existing ARM CMSIS headers remain under the
project's `CMSIS/` directory with their original notices.

The Keil project references these files using paths relative to `MDK-ARM/`.
Building requires Keil ARMCC 5 and the STM32F1 device support pack, but does not
require a personal STM32Cube firmware repository. Regenerating code with CubeMX
still requires the matching STM32CubeF1 package.

The `.ioc` uses **Copy only the necessary library files** (`LibraryCopy=1`).
After any regeneration, check for duplicate HAL sources, external paths, and
preservation of the project's custom interrupt and task glue before building.
