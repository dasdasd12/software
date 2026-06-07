---
name: wch-mrs-automation
description: CH32H417-dedicated automation for MounRiver Studio 2, WCH-Link, OpenOCD, and riscv-wch-elf/riscv32-wch-elf toolchains. Handles dual-core V3F/V5F build, flash, and debug flows; generates Makefiles and VS Code/MRS tasks; diagnoses WCH-Link, OpenOCD, GDB, or MRS project issues.
---

# WCH MRS Automation (CH32H417)

Use the bundled PowerShell script first:

```powershell
.\scripts\wch-auto.ps1 -Action detect -ProjectDir <project>
.\scripts\wch-auto.ps1 -Action detect -ProjectDir <project> -Diagnose      # full link probe
.\scripts\wch-auto.ps1 -Action init -ProjectDir <project>
.\scripts\wch-auto.ps1 -Action build -ProjectDir <project> -Core both
.\scripts\wch-auto.ps1 -Action flash -ProjectDir <project> -Core both
.\scripts\wch-auto.ps1 -Action flash -ProjectDir <project> -Core both -DryRun
.\scripts\wch-auto.ps1 -Action debug-check -ProjectDir <project> -Core v3f
.\scripts\wch-auto.ps1 -Action debug -ProjectDir <project> -Core v5f
.\scripts\wch-auto.ps1 -Action recover -ProjectDir <project>               # chip locked?
.\scripts\wch-auto.ps1 -Action reset-link                                   # soft-then-PnP
```

Every run writes a session log under `<project>\.wch-skill-logs\session-YYYYMMDD-HHmmss.log` plus a paired `mrs-trace-*.log` capturing the return code + duration of every MRS DLL call. When the chip locks, read those logs first — they pinpoint which call broke.

The script discovers MounRiver Studio 2 under `C:\MounRiver\MounRiver_Studio2`, its WCH OpenOCD, GCC12/GCC15 toolchains, GDB, and bundled `make.exe`. It defaults to `CH32H417`; you do not need to pass `-Chip` unless overriding auto-detection.

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
- Flash both cores as two ELF files (V5F first, then V3F, then resume), then reset/run.
- A V5F-only image usually does not start by itself after reset unless a V3F image wakes it.

For more detail, read `references/h417-dual-core.md`.

## Workflow

1. Run `detect` (add `-Diagnose` for a full link/RDP probe).
2. If the project has no command-line Makefile, run `init`. Pass `-EVTRoot` if the EVT package is not at `C:\program1\hardware\WCH\CH32H417\CH32H417EVT\EVT\EXAM`.
3. Run `build`.
4. Run `flash -DryRun` to inspect the command, then run `flash` only when overwriting the connected board is intended.
5. Use `debug-check` for noninteractive register/connection validation. Use `debug` for an interactive GDB session.
6. If a flash or debug step reports `WCH-Link failed to connect`, run `-Action recover` first. It tries an in-process MRS rehandshake, only falls back to a USB cycle if needed, then forces a full chip erase so the next `flash` can land cleanly.

## Flash defaults (Layer B alignment, 2026-05)

- `-ClkSpeed` defaults to `Middle` (~3 MHz SDI). `High` was marginal at 400 MHz core clock on PB8/PB9.
- The MRS operation byte mirrors the MRS GUI EVT defaults: full chip erase + program + verify; **read-protect is NOT touched** unless you pass `-DisableCodeProtect`. (Old default would set bit `0x20` every flash, repeatedly rewriting the option-byte region; that was the primary suspect for "chip locked after a few flashes".)
- Pass `-NoEraseAll` to skip the full erase (e.g., for a V5F image that must not wipe V3F's bank — the dual-core flow already does this internally).
- `flash` no longer kills a live OpenOCD daemon. MRS DLL flash coexists with a running OpenOCD; killing it forced a fresh OpenOCD on the next `debug-check`, which is what triggered the second-init lock.
- `debug-check` no longer runs `Invoke-MRSSetTarget` / `Invoke-MRSReset` immediately before launching OpenOCD. Pass `-AllowMrsReset` only when V3F has entered STOP mode and OpenOCD `init` is failing.
- `mrs-link.ps1` no longer calls `SetSDLineMode` on every flash; it is only invoked by the explicit `set-line-mode` action.

## Lock recovery

If a flash succeeds once but the next flash or `debug-check` fails with `WCH-Link failed to connect with riscvchip`:

```powershell
.\scripts\wch-auto.ps1 -Action detect -ProjectDir <project> -Diagnose
.\scripts\wch-auto.ps1 -Action recover -ProjectDir <project>
.\scripts\wch-auto.ps1 -Action flash -ProjectDir <project> -Core both
```

`-Action recover` runs: MRS rehandshake → USB PnP cycle (only if rehandshake fails) → `query-rprotect` → `disable-rprotect` → forced full-chip erase via a 4-byte stub `.bin`. Its full call sequence is captured in the trace log.

## Reference Commands

Build both H417 cores (example uses the EVT HSEM_CoreSync sample; substitute your own project):

```powershell
.\scripts\wch-auto.ps1 -Action build -ProjectDir C:\program1\hardware\WCH\CH32H417\CH32H417EVT\EVT\EXAM\CPU\HSEM\HSEM_CoreSync -Core both
```

Flash both H417 cores:

```powershell
.\scripts\wch-auto.ps1 -Action flash -ProjectDir <project> -Core both
```

Attach/check V3F and V5F:

```powershell
.\scripts\wch-auto.ps1 -Action debug-check -ProjectDir <project> -Core v3f
.\scripts\wch-auto.ps1 -Action debug-check -ProjectDir <project> -Core v5f
```

Run core checks sequentially. If V3F was checked with reset/halt first, run the board again before checking V5F so V3F can wake the secondary core.

## Known Behaviours

- **Default flash uses MRS DLL** – The skill defaults to MRS DLL flash (not OpenOCD) for all cores, including dual-core. This avoids the OpenOCD second-init bug.
- **OpenOCD daemon reuse** – `debug-check` reuses an existing OpenOCD daemon or starts one and leaves it running. Do not manually kill OpenOCD between flash and debug if you want to avoid re-plugging.
- **OpenOCD `Unsupported register` warning** – During dual-core flash you may see `Error: Unsupported register (enum gdb_regno)(8)`. This is a benign OpenOCD quirk; programming and verification still succeed.
- **V5F debug-check needs V3F awake** – After a V3F `reset halt`, V5F sits at `0x00000000` because it was never woken. Flash/run the board before checking V5F so V3F can wake it.
- **OpenOCD second-init bug** – After any OpenOCD process exits, a subsequent OpenOCD start often fails with `WCH-Link failed to connect with riscvchip`. The skill avoids this by (a) keeping the OpenOCD daemon alive across flash, and (b) skipping the pre-flash MRS DLL reset unless `-AllowMrsReset` is passed. If it still happens, run `-Action recover`.
- **`reset-link` is rehandshake-first** – No longer immediately disables/enables the USB device. It calls `OpenDevice` → `CompareVersion` → `CloseDevice` first; only if that fails does it fall back to the PnP cycle.

## Common Fixes

- If OpenOCD cannot open WCH-Link, check the cable, target power, and WCH-Link mode.
- If OpenOCD fails with `WCH-Link failed to connect with riscvchip`, run `-Action recover`. Use `-RestartLink` on the next flash only if `recover` is not appropriate.
- If port `3333` or `3334` is busy, stop old OpenOCD/GDB processes.
- If a copied EVT project cannot find `SRC`, run `init` with the correct `-EVTRoot`.
- If an H417 V5F image verifies but does not run, also flash/run the V3F image that wakes V5F.
- Use official WCH startup and linker scripts. Do not replace H417 startup with a minimal XIP startup.
- After any unexpected lock, attach the matching `.wch-skill-logs/session-*.log` and `mrs-trace-*.log` to the report — they show every DLL call, return code, and timing.
