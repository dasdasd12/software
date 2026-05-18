---
name: wch-mrs-automation
description: Automate WCH RISC-V MCU projects that use MounRiver Studio 2, WCH-Link, OpenOCD, and riscv-wch-elf/riscv32-wch-elf toolchains. Use for CH32H417 dual-core V3F/V5F build, flash, and debug flows; CH585/CH32V single-core build, flash, and debug flows; generating Makefiles and VS Code/MRS tasks; diagnosing WCH-Link, OpenOCD, GDB, or MRS project issues.
---

# WCH MRS Automation

Use the bundled PowerShell script first:

```powershell
.\scripts\wch-auto.ps1 -Action detect -ProjectDir <project>
.\scripts\wch-auto.ps1 -Action init -ProjectDir <project> -Chip CH32H417
.\scripts\wch-auto.ps1 -Action build -ProjectDir <project> -Chip CH32H417 -Core both
.\scripts\wch-auto.ps1 -Action flash -ProjectDir <project> -Chip CH32H417 -Core both
.\scripts\wch-auto.ps1 -Action flash -ProjectDir <project> -Chip CH32H417 -Core both -DryRun
.\scripts\wch-auto.ps1 -Action debug-check -ProjectDir <project> -Chip CH32H417 -Core v3f
.\scripts\wch-auto.ps1 -Action debug -ProjectDir <project> -Chip CH32H417 -Core v5f
```

The script discovers MounRiver Studio 2 under `C:\MounRiver\MounRiver_Studio2`, its WCH OpenOCD, GCC12/GCC15 toolchains, GDB, and bundled `make.exe`.

## CH32H417 Dual Core

Prefer the official EVT layout:

```text
<project>\
  Common\
  V3F\User\
  V5F\User\
```

The CH32H417 EVT examples show this boot/debug mapping:

- V3F uses `startup_ch32h417_v3f.S`, `Link_v3f.ld`, flash origin `0x00000000`, OpenOCD target `wch_riscv.cpu.0`, GDB port `3333`.
- V5F uses `startup_ch32h417_v5f.S`, `Link_v5f.ld`, flash origin `0x00010000`, OpenOCD target `wch_riscv.cpu.1`, GDB port `3334`.
- V3F is the boot/wake coordinator in the EVT examples. It wakes V5F with `NVIC_WakeUp_V5F(Core_V5F_StartAddr)`.
- Flash both cores as two ELF files, V3F first and V5F second, then reset/run.
- A V5F-only image usually does not start by itself after reset unless a V3F image wakes it.

For more detail, read `references/h417-dual-core.md`.

## Workflow

1. Run `detect`.
2. If the project has no command-line Makefile, run `init`. For CH32H417 dual-core projects, pass `-EVTRoot` if the EVT package is not at `C:\program1\hardware\WCH\CH32H417\CH32H417EVT\EVT\EXAM`.
3. Run `build`.
4. Run `flash -DryRun` to inspect the OpenOCD command, then run `flash` only when overwriting the connected board is intended.
5. Use `debug-check` for noninteractive register/connection validation. Use `debug` for an interactive GDB session.

## Reference Commands

Build both H417 cores:

```powershell
.\scripts\wch-auto.ps1 -Action build -ProjectDir C:\program1\Program\H417lib\HSEM_CoreSync -Chip CH32H417 -Core both
```

Flash both H417 cores:

```powershell
.\scripts\wch-auto.ps1 -Action flash -ProjectDir C:\program1\Program\H417lib\HSEM_CoreSync -Chip CH32H417 -Core both
```

Attach/check V3F and V5F:

```powershell
.\scripts\wch-auto.ps1 -Action debug-check -ProjectDir <project> -Chip CH32H417 -Core v3f
.\scripts\wch-auto.ps1 -Action debug-check -ProjectDir <project> -Chip CH32H417 -Core v5f
```

Run core checks sequentially. If V3F was checked with reset/halt first, run the board again before checking V5F so V3F can wake the secondary core.

## Common Fixes

- If OpenOCD cannot open WCH-Link, check the cable, target power, and WCH-Link mode.
- If port `3333` or `3334` is busy, stop old OpenOCD/GDB processes.
- If a copied EVT project cannot find `SRC`, run `init` with the correct `-EVTRoot`.
- If an H417 V5F image verifies but does not run, also flash/run the V3F image that wakes V5F.
- Use official WCH startup and linker scripts. Do not replace H417 startup with a minimal XIP startup.
