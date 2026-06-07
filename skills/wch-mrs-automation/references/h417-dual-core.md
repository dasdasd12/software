# CH32H417 Dual-Core Notes

Use this reference when a task involves CH32H417 V3F/V5F build, flash, or debug.

## Source Of Truth

The local EVT examples under `C:\program1\hardware\WCH\CH32H417\CH32H417EVT\EVT\EXAM` define the practical command-line mapping. In particular, compare:

- `GPIO\GPIO_Toggle\V3F\GPIO_Toggle_V3F.wvproj`
- `GPIO\GPIO_Toggle\V5F\GPIO_Toggle_V5F.wvproj`
- `CPU\HSEM\HSEM_CoreSync`
- `CPU\IPC\IPC`
- `SRC\Ld\V3F\Link_v3f.ld`
- `SRC\Ld\V5F\Link_v5f.ld`
- MRS OpenOCD `wch-dual-core.cfg`

## Core Mapping

| Core | EVT role | Startup | Linker | Flash origin | OpenOCD target | GDB port |
| --- | --- | --- | --- | --- | --- | --- |
| V3F | boot/wake coordinator in EVT projects | `startup_ch32h417_v3f.S` | `Link_v3f.ld` | `0x00000000` | `wch_riscv.cpu.0` | `3333` |
| V5F | secondary core in EVT projects | `startup_ch32h417_v5f.S` | `Link_v5f.ld` | `0x00010000` | `wch_riscv.cpu.1` | `3334` |

This mapping follows the EVT `.wvproj` debug settings: V3F has `isMaster: true` and uses `masterGDBPort: 3333`; V5F uses `slaveGDBPort: 3334`.

## Build Requirements

Use the same compiler flags as MRS-generated H417 examples:

```make
-march=rv32imac_zba_zbb_zbc_zbs_xw -mabi=ilp32
-msmall-data-limit=8 -msave-restore
-Os -fsigned-char -ffunction-sections -fdata-sections -fno-common
```

Use core-specific defines:

```make
-DCore_V3F
-DCore_V5F
```

Build each core with its own startup and linker script. Do not compile both startup files into one ELF.

## Flash Sequence

**Recommended:** Use the MRS DLL for all flash operations. It handles dual-core addressing correctly and does not trigger the WCH-Link second-init bug.

```powershell
.\wch-auto.ps1 -Action flash -ProjectDir <project> -Chip CH32H417 -Core both
```

The skill defaults to MRS DLL flash. For dual-core it flashes V3F first (address `0x08000000`, erase), then V5F (address `0x08010000`, no erase, reset run). The V5F reset boots V3F, which calls `NVIC_WakeUp_V5F` to start the secondary core cleanly.

### MRS DLL Flash Addresses

The skill passes the correct base addresses for dual-core:
- V3F image: `0x08000000`
- V5F image: `0x08010000`

This matches the Intel hex segment base in the V5F hex file (`:020000021000EC`).

### OpenOCD Flash (fallback only)

Only use OpenOCD flash if MRS DLL is unavailable (`-FlashTool openocd`). Be aware that OpenOCD flash exits the process, which triggers the second-init bug described below.

For a complete dual-core firmware via OpenOCD:

```powershell
openocd.exe -s <openocd-bin> -f wch-dual-core.cfg `
  -c "init" `
  -c "targets wch_riscv.cpu.1" -c "halt" -c "program <v5f.elf> verify" `
  -c "targets wch_riscv.cpu.0" -c "halt" -c "program <v3f.elf> verify" `
  -c "resume" -c "exit"
```

## Debug Sequence

The skill manages OpenOCD as a daemon:

```powershell
.\wch-auto.ps1 -Action debug-check -ProjectDir <project> -Chip CH32H417 -Core v3f
.\wch-auto.ps1 -Action debug-check -ProjectDir <project> -Chip CH32H417 -Core v5f
```

- If an OpenOCD daemon is already running, the skill **reuses** it instead of starting a second instance.
- If no daemon is running, the skill starts one and **leaves it running** after `debug-check`.
- This avoids the second-init bug described below.

For manual GDB connection to an already-running daemon:

```powershell
riscv-wch-elf-gdb.exe <v3f.elf> -ex "target remote localhost:3333"
riscv-wch-elf-gdb.exe <v5f.elf> -ex "target remote localhost:3334"
```

For noninteractive validation, avoid `continue`; it blocks forever in normal embedded loops. Prefer:

```gdb
target remote localhost:<port>
monitor reset halt
info registers pc sp gp
quit
```

Check V3F and V5F sequentially, not in parallel, because both sessions share one WCH-Link and one OpenOCD server port set. A V3F check that uses `reset halt` can stop the boot core before it wakes V5F. To validate V5F after that, flash/run the board first so V3F can wake the secondary core, then connect to `localhost:3334` and use `monitor halt` instead of `monitor reset halt`.

## MounRiver IDE Debug Options

The EVT `.wvproj` files show how MounRiver configures OpenOCD per core:

| Core | OpenOCD extra options | skipDownloadBeforeDebug |
|------|----------------------|------------------------|
| V3F  | `-c noload`          | `true`                 |
| V5F  | `-c page_erase`      | `false`                |

The skill applies these same options when it starts OpenOCD for each core:
- **V3F**: `-c noload` prevents OpenOCD from reloading the image before debug (the V3F image is already in flash).
- **V5F**: `-c page_erase` enables page erase for the secondary core.

MounRiver debug settings also use:
- V3F: `initResetType: init`, `runResetType: halt`, `setBreakAt: BP`
- V5F: `initResetType: init`, `runResetType: halt`, `setBreakAt: handle_reset`

## Known Behaviours

### OpenOCD `Unsupported register` warning

During dual-core flash you may see:

```
Error: Unsupported register (enum gdb_regno)(8)
```

This is a benign OpenOCD quirk on the WCH RISC-V target. Programming and verification still succeed (`Programming Finished`, `Verified OK`). Do not treat this as a failure.

### V5F shows PC = 0x00000000 after V3F reset/halt

If you connect to V5F after a V3F `reset halt` (or after power-on before V3F has run), V5F has not yet been woken. Its PC reads `0x00000000` and SP/GP may show default reset values. This is expected. Run `reset run` (or re-flash with resume) so V3F executes NVIC_WakeUp_V5F, then check V5F again.

### V3F STOP mode destabilises OpenOCD

When V3F enters `PWR_EnterSTOPMode` (which uses `__WFE`), the debug module may become unresponsive. Specific symptoms:

- `openocd ... -c "reset run"` crashes with assertion failure or `dmstatus=0xffffffff`
- Sequential GDB connections to V3F then V5F can trigger "Debugger is not authenticated" errors
- `monitor reset halt` on a sleeping V3F may report a stale PC instead of the reset vector

**Workarounds:**
- Prefer MRS DLL flash (`-FlashTool mrs`) over OpenOCD when V3F uses STOP mode; the MRS helper recovers from bad debug-module states automatically
- If OpenOCD enters a crash loop, kill all `openocd.exe` and `riscv-wch-elf-gdb.exe` processes, then re-plug the WCH-Link
- For debug-check after STOP mode, expect `info registers` to show the halted execution address (e.g. `__WFE`) rather than the reset vector; this is normal because `reset halt` does not reliably reset a core that is in WFE

### OpenOCD cannot be restarted after exit

A severe WCH-LinkE firmware limitation affects OpenOCD 0.11.0+dev-snapshot shipped with MRS2:

- The **first** OpenOCD process after a fresh WCH-Link plug-in can `init` and operate normally.
- Once that OpenOCD process **exits**, a subsequent OpenOCD process will fail during `wlink_init()` with `WCH-Link failed to connect with riscvchip`.
- This happens **even if V3F is running** (not in STOP mode) and even after stale-process cleanup and multi-second delays.
- Re-plugging WCH-Link is the brute-force cure, but the skill's `-Action recover` recovers without a physical re-plug in most cases.

**Practical impact:**
- `flash` (OpenOCD) followed by `debug-check` (OpenOCD) will always fail the second step.
- MRS DLL flash does not suffer from this, but MRS DLL itself can later fail with error 104 if OpenOCD has already triggered the bug.

**Skill workaround (automated):**
The skill defaults to MRS DLL for all flash operations, avoiding OpenOCD exit during flash. For `debug-check`, it reuses an existing OpenOCD daemon or starts one and **leaves it running**, preventing the second-init scenario in the typical workflow. The pre-flash MRS reset (which used to run immediately before launching OpenOCD and was itself a trigger for the second-init bug) is now opt-in via `-AllowMrsReset`.

If OpenOCD does fail to start (port does not open), the skill recovers in this order:

```powershell
# Preferred — in-process rehandshake first, USB cycle only if needed,
# then force a full chip erase via the MRS DLL.
.\wch-auto.ps1 -Action recover -ProjectDir <project>

# `reset-link` is the same recovery without the forced erase.
.\wch-auto.ps1 -Action reset-link

# Or, opportunistically as part of the next operation:
.\wch-auto.ps1 -Action debug-check -ProjectDir <project> -Core v3f -RecoverMode
```

`reset-link` (and `recover`'s first phase) calls `OpenDevice` / `CompareVersion` / `CloseDevice` via the MRS DLL first. Only if that fails does it fall back to `Disable-PnpDevice` / `Enable-PnpDevice` on the WCH-Link USB composite device. PnP cycling mid-transfer was observed to leave the link half-dead, so demoting it to a last resort is intentional.

**Recommended workflow:**
1. Flash with MRS DLL: `.\wch-auto.ps1 -Action flash -Core both`
2. Debug-check reuses or starts the daemon: `.\wch-auto.ps1 -Action debug-check -Core v3f`
3. The daemon stays alive. Subsequent debug-checks connect to it directly.
4. If the link locks anyway, run `-Action recover` and rerun `flash`. Inspect the matching `.wch-skill-logs/session-*.log` + `mrs-trace-*.log` to see which DLL call failed.

For projects where V3F uses STOP mode, pass `-AllowMrsReset` on `debug-check` so the skill runs an MRS-side reset to wake V3F before launching OpenOCD.

### OpenOCD cannot attach while user firmware runs at speed

Confirmed on hardware 2026-05-26 with the `rtthread_port` project (V3F wakeup + V5F running RT-Thread at 400 MHz on PB8/PB9 SDI):

- After `-Action flash -Core both`, the chip immediately runs the user firmware. V5F is at 400 MHz; V3F is in STOP after waking V5F.
- `MRS DLL` calls still succeed: `OpenDevice` / `CompareVersion` / `MRSFunc_FlashOperationExB` (with the `0x40` clear-code-flash escalation) all return 0.
- `OpenOCD`'s `wlink_init` returns `WCH-Link failed to connect with riscvchip` for both `wch_riscv.cpu.0` and `wch_riscv.cpu.1`.
- `-RecoverMode` runs the in-process MRS rehandshake (which succeeds), then re-launches OpenOCD — which fails identically.

Root cause is **driver-asymmetric**: the MRS DLL uses an aggressive SDI entry sequence (NRST pulse + option-byte halt path, same as `clear-code-flash`) that re-establishes SDI even when the CPU is busy. OpenOCD 0.11.0+dev-snapshot's `wlink` driver does not implement this and silently fails the handshake against a running chip.

**Confirmed NOT fixable from skill side** (investigated 2026-05-26):
- `McuCompiler_SetRSTPin(0/1)` exists in `McuCompilerDll.dll` v2.7 (error 117 in the MRS error table = "Failed to operate RST pin output") and returns 0 in both directions — the DLL primitive works.
- Holding NRST low via MRS before launching OpenOCD does NOT make `wlink_init` succeed. Holding NRST high doesn't either. Pulsing then racing OpenOCD against firmware boot loses every time (the ~1.5 s WoW64 re-exec for `mrs-link.ps1` is far slower than V3F's wakeup).
- PnP cycling the WCH-LinkE composite device (when admin is available) doesn't help — the adapter's `mode:RV version 2.18` detection succeeds; the failure is in the chip-side handshake step.
- `mrs-link.ps1` exposes `-Action hold-nrst` and `-Action release-nrst` as building blocks should a future OpenOCD release expose a way to consume external NRST control. Not wired into `debug-check` today.

**Practical impact:** Pure-OpenOCD `debug-check` against a chip already running this firmware is **not currently recoverable** without one of:
1. A `flash` of an idle/halt image before debug-check, or
2. Hold BOOT0 high + power-cycle, then debug-check before V3F runs (i.e. before NRST is released), or
3. A future fix in OpenOCD's `wlink_init` to mirror the MRS DLL's halt sequence.

The skill does not paper over this — it surfaces the failure with a clear message and points at `-Action recover`. Day-to-day flash/run loops are unaffected because MRS DLL flash works regardless of CPU state.

## Startup Caveat

Keep the official WCH startup files. H417 startup copies code into RAM_CODE, configures flash acceleration, initializes global pointer/CSR state, and then enters C code. Minimal XIP startup is not a safe replacement for these examples.

## Clock Configuration Caveat

On some boards, `SystemInit()` with HSE-based PLL configuration hangs waiting for HSE ready. If this occurs, switch to the HSI-based variant in `system_ch32h417.c`:

```c
// #define SYSCLK_400M_CoreCLK_V5F_400M_V3F_100M_HSE    400000000
#define SYSCLK_400M_CoreCLK_V5F_400M_V3F_100M_HSI    400000000
```

Removing `SystemInit()` entirely is unsafe because Flash wait states (`FLASH_ACTLR_LATENCY_HCLK_DIV2`) are not configured, causing instruction-fetch corruption at 100 MHz PLL speed.
