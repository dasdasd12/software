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

For a complete dual-core firmware, program V3F first, then V5F, then reset/run:

```powershell
openocd.exe -s <openocd-bin> -f wch-dual-core.cfg `
  -c "init" `
  -c "targets wch_riscv.cpu.0" -c "halt" -c "program <v3f.elf> verify" `
  -c "targets wch_riscv.cpu.1" -c "halt" -c "program <v5f.elf> verify" `
  -c "reset run" -c "exit"
```

Use `wch-auto.ps1 -Action flash -Core both -DryRun` to print the equivalent command without touching the board.

For V3F-only:

```powershell
openocd.exe -s <openocd-bin> -f wch-dual-core.cfg `
  -c "init" -c "targets wch_riscv.cpu.0" -c "halt" `
  -c "program <v3f.elf> verify reset" -c "exit"
```

For V5F-only:

```powershell
openocd.exe -s <openocd-bin> -f wch-dual-core.cfg `
  -c "init" -c "targets wch_riscv.cpu.1" -c "halt" `
  -c "program <v5f.elf> verify" -c "exit"
```

V5F-only flashing is useful for development, but reset/run normally still needs a V3F program that wakes V5F.

## Debug Sequence

Start OpenOCD once:

```powershell
openocd.exe -s <openocd-bin> -f wch-dual-core.cfg
```

Connect GDB to the core being debugged:

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

Check V3F and V5F sequentially, not in parallel, because both sessions share one WCH-Link and one OpenOCD server port set. A V3F check that uses `reset halt` can stop the boot core before it wakes V5F. To validate V5F after that, run the board first:

```powershell
openocd.exe -s <openocd-bin> -f wch-dual-core.cfg -c init -c "reset run" -c "sleep 3000" -c exit
```

Then connect to `localhost:3334` and use `monitor halt` instead of `monitor reset halt`.

## Startup Caveat

Keep the official WCH startup files. H417 startup copies code into RAM_CODE, configures flash acceleration, initializes global pointer/CSR state, and then enters C code. Minimal XIP startup is not a safe replacement for these examples.
