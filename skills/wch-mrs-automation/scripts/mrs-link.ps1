#requires -Version 5.1
<#
.SYNOPSIS
    Minimal command-line wrapper for the MRS WCH CommunicationLib DLL.

.NOTES
    MounRiver Studio 2 ships a 32-bit McuCompilerDll.dll. This script
    re-launches itself under 32-bit Windows PowerShell when needed.
#>
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("set-target", "set-line-mode", "get-chip-id", "query-rprotect", "disable-rprotect", "flash", "reset", "dump-link-status", "rehandshake", "hold-nrst", "release-nrst")]
    [string]$Action,

    [int]$ChipID = 198,

    [ValidateSet("one-wire", "two-wire")]
    [string]$DebugInterface = "one-wire",

    [ValidateSet("High", "Middle", "Low")]
    [string]$ClkSpeed = "High",

    [int]$OperationType = 0x27,

    [string]$DataFilePath = "",
    [string]$FlashAddress = "0x08000000",

    [string]$CommunicationLibPath = "C:\MounRiver\MounRiver_Studio2\resources\app\resources\win32\components\WCH\Others\CommunicationLib\default",
    [string]$FirmwareLinkPath = "C:\MounRiver\MounRiver_Studio2\resources\app\resources\win32\components\WCH\Others\Firmware_Link\default",

    [string]$TraceLog = "",

    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

if (-not $TraceLog) {
    $TraceLog = Join-Path $env:TEMP "wch-mrs-trace.log"
}

function Write-MrsTrace {
    param([string]$Line)
    $stamp = (Get-Date -Format "yyyy-MM-dd HH:mm:ss.fff")
    $pid32 = if ([Environment]::Is64BitProcess) { "ps64" } else { "ps32" }
    try {
        Add-Content -LiteralPath $TraceLog -Value "[$stamp][$pid32] $Line" -ErrorAction SilentlyContinue
    } catch {}
}

function Write-MrsInfo {
    param([string]$Message)
    Write-Host "[MRS]  $Message"
    Write-MrsTrace "INFO  $Message"
}

function Write-MrsWarn {
    param([string]$Message)
    Write-Host "[WARN] $Message" -ForegroundColor Yellow
    Write-MrsTrace "WARN  $Message"
}

function Invoke-MrsCall {
    <#
    Wraps a P/Invoke and records its return code + duration to the trace log.
    The scriptblock must return an integer.
    #>
    param(
        [string]$Name,
        [scriptblock]$Call
    )
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $ret = -1
    try {
        $ret = & $Call
    } catch {
        $sw.Stop()
        Write-MrsTrace ("CALL  {0} EXCEPTION {1} (after {2}ms)" -f $Name, $_.Exception.Message, $sw.ElapsedMilliseconds)
        throw
    }
    $sw.Stop()
    Write-MrsTrace ("CALL  {0} ret={1} ({2}ms)" -f $Name, $ret, $sw.ElapsedMilliseconds)
    return $ret
}

function Get-DebugMode {
    param([string]$Mode)
    if ($Mode -eq "one-wire") { return 0 }
    return 1
}

function Get-ClockSpeedValue {
    param([string]$Speed)
    switch ($Speed) {
        "High" { return 1 }
        "Middle" { return 2 }
        "Low" { return 3 }
        default { return 1 }
    }
}

function Convert-AddressToInt {
    param([string]$Address)
    $text = $Address.Trim()
    if ($text -match "^0x[0-9a-fA-F]+$") {
        return [Convert]::ToInt32($text.Substring(2), 16)
    }
    return [Convert]::ToInt32($text, 10)
}

function Get-RProtectText {
    param([int]$Status)
    switch ($Status) {
        3 { return "enabled" }
        4 { return "disabled" }
        default { return "unknown($Status)" }
    }
}

function Get-MRSErrorText {
    param([int]$Code)
    switch ($Code) {
        0 { return "Operation Success." }
        100 { return "Failed to open WCH-Link." }
        101 { return "Failed to get WCH-Link version." }
        102 { return "Current WCH-Link does not support this chip." }
        103 { return "Failed to configure two-line speed." }
        104 { return "Failed to configure MCU or SWD communication status." }
        105 { return "Failed to query Code-Protect status." }
        106 { return "Failed to disable Code-Protect status." }
        107 { return "Failed to enable Code-Protect status." }
        108 { return "Failed to reset MCU." }
        109 { return "Failed to erase all." }
        110 { return "Failed to program or verify." }
        111 { return "Failed to erase all code flash." }
        112 { return "Failed to get ROM/RAM allocation." }
        113 { return "Failed to set ROM/RAM allocation." }
        114 { return "Failed to disable two-line debug interface." }
        115 { return "Current chip does not support erasing all code flash." }
        116 { return "Failed to operate WCH-Link power output." }
        117 { return "Failed to operate RST pin output." }
        118 { return "Failed to enable SDI print function." }
        120 { return "Before operation, ensure Code-Protect is disabled." }
        121 { return "Failed to transform HEX to BIN." }
        122 { return "Invalid target file name." }
        123 { return "Current chip does not support 2-wires serial debug mode." }
        124 { return "Failed to set 2-wires serial debug mode." }
        125 { return "Current chip does not support erase CodeFlash by NRST." }
        126 { return "Current chip does not support erase CodeFlash by power off." }
        127 { return "Current chip does not support closing two-line debug interface." }
        128 { return "Communication failure or chip status error." }
        129 { return "Communication timeout." }
        130 { return "Chip type mismatch." }
        131 { return "Programming address exceeds flash range." }
        132 { return "Invalid programming address." }
        default { return "Unknown error $Code." }
    }
}

function Get-OperationDescription {
    param([int]$Operation)
    $parts = New-Object System.Collections.Generic.List[string]
    if (($Operation -band 0x80) -ne 0) { [void]$parts.Add("disable-power-output") }
    if (($Operation -band 0x40) -ne 0) { [void]$parts.Add("clear-code-flash") }
    if (($Operation -band 0x20) -ne 0) { [void]$parts.Add("disable-code-protect") }
    if (($Operation -band 0x10) -ne 0) { [void]$parts.Add("sdi-printf") }
    if (($Operation -band 0x08) -ne 0) { [void]$parts.Add("erase-all") }
    if (($Operation -band 0x04) -ne 0) { [void]$parts.Add("program") }
    if (($Operation -band 0x02) -ne 0) { [void]$parts.Add("verify") }
    if (($Operation -band 0x01) -ne 0) { [void]$parts.Add("reset-run") }
    if ($parts.Count -eq 0) { return "none" }
    return ($parts -join ",")
}

function Restart-UnderWow64IfNeeded {
    if (-not [Environment]::Is64BitProcess) { return }

    $ps32 = Join-Path $env:WINDIR "SysWOW64\WindowsPowerShell\v1.0\powershell.exe"
    if (-not (Test-Path -LiteralPath $ps32)) {
        throw "MRS CommunicationLib is 32-bit, but 32-bit Windows PowerShell was not found: $ps32"
    }

    $args = @(
        "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", $PSCommandPath,
        "-Action", $Action,
        "-ChipID", $ChipID,
        "-DebugInterface", $DebugInterface,
        "-ClkSpeed", $ClkSpeed,
        "-OperationType", $OperationType,
        "-FlashAddress", $FlashAddress,
        "-CommunicationLibPath", $CommunicationLibPath,
        "-FirmwareLinkPath", $FirmwareLinkPath,
        "-TraceLog", $TraceLog
    )
    if ($DataFilePath) { $args += @("-DataFilePath", $DataFilePath) }
    if ($DryRun) { $args += "-DryRun" }

    & $ps32 @args
    exit $LASTEXITCODE
}

Restart-UnderWow64IfNeeded

$debugMode = Get-DebugMode $DebugInterface
$clk = Get-ClockSpeedValue $ClkSpeed
$flashAddr = Convert-AddressToInt $FlashAddress
$dllPath = Join-Path $CommunicationLibPath "McuCompilerDll.dll"

if (-not (Test-Path -LiteralPath $dllPath)) {
    throw "MRS CommunicationLib DLL not found: $dllPath"
}

if ($Action -eq "flash") {
    if (-not $DataFilePath) { throw "DataFilePath is required for flash." }
    if (-not (Test-Path -LiteralPath $DataFilePath)) { throw "Target file not found: $DataFilePath" }
    $ext = [System.IO.Path]::GetExtension($DataFilePath).ToLowerInvariant()
    if ($ext -ne ".hex" -and $ext -ne ".bin") {
        throw "MRS DLL flash only supports .hex and .bin files: $DataFilePath"
    }
}

if ($DryRun) {
    Write-MrsInfo "Dry run: action=$Action chip=$ChipID interface=$DebugInterface clk=$ClkSpeed"
    Write-MrsInfo "Trace log would be: $TraceLog"
    if ($Action -eq "flash") {
        Write-MrsInfo ("operation=0x{0:X2} ({1}) address=$FlashAddress file=$DataFilePath" -f $OperationType, (Get-OperationDescription $OperationType))
    }
    exit 0
}

$env:PATH = "$CommunicationLibPath;$env:PATH"
$oldTmp = $env:TMP
$oldTemp = $env:TEMP
if (Test-Path -LiteralPath "C:\tmp") {
    $env:TMP = "C:\tmp"
    $env:TEMP = "C:\tmp"
}

$dllLiteral = $dllPath.Replace('"', '""')
$code = @"
using System;
using System.Runtime.InteropServices;

public static class MRSNativeLink {
    [DllImport("kernel32.dll", SetLastError=true, CharSet=CharSet.Unicode)]
    public static extern bool SetDllDirectory(string lpPathName);

    [DllImport(@"$dllLiteral", CallingConvention=CallingConvention.StdCall)]
    public static extern int McuCompiler_OpenDevice();

    [DllImport(@"$dllLiteral", CallingConvention=CallingConvention.StdCall)]
    public static extern int McuCompiler_CloseDevice();

    [DllImport(@"$dllLiteral", CallingConvention=CallingConvention.StdCall)]
    public static extern int McuCompiler_SetTargetChip(int chipType, int debugMode);

    [DllImport(@"$dllLiteral", CallingConvention=CallingConvention.StdCall)]
    public static extern int McuCompiler_SetSDLineMode(int debugMode);

    [DllImport(@"$dllLiteral", CallingConvention=CallingConvention.StdCall, CharSet=CharSet.Ansi)]
    public static extern int McuCompiler_CompareVersion([MarshalAs(UnmanagedType.LPStr)] string cfgFile, ref int linkType, ref int linkMode);

    [DllImport(@"$dllLiteral", CallingConvention=CallingConvention.StdCall)]
    public static extern int MRSFunc_QueryRProtect(int chipType, int clkSpeed);

    [DllImport(@"$dllLiteral", CallingConvention=CallingConvention.StdCall)]
    public static extern int MRSFunc_GetLinkedMCUID();

    [DllImport(@"$dllLiteral", CallingConvention=CallingConvention.StdCall)]
    public static extern int MRSFunc_DisableRProtect(int chipType, int clkSpeed);

    [DllImport(@"$dllLiteral", CallingConvention=CallingConvention.StdCall)]
    public static extern int McuCompiler_ResetB(int chipType, int debugMode);

    [DllImport(@"$dllLiteral", CallingConvention=CallingConvention.StdCall)]
    public static extern int McuCompiler_SetRSTPin(int level);

    [DllImport(@"$dllLiteral", CallingConvention=CallingConvention.StdCall, CharSet=CharSet.Ansi)]
    public static extern int MRSFunc_FlashOperationExB(int chipType, int clkSpeed, int operaType, int addrSel, [MarshalAs(UnmanagedType.LPStr)] string dataFileName);
}
"@

$deviceOpened = $false
$maxOpenAttempts = 2
$maxFlashAttempts = 2

try {
    Add-Type -TypeDefinition $code
    [MRSNativeLink]::SetDllDirectory($CommunicationLibPath) | Out-Null

    Write-MrsInfo "Host is 32-bit PowerShell: $(-not [Environment]::Is64BitProcess)"
    Write-MrsInfo "Trace log: $TraceLog"
    Write-MrsTrace "ENTER action=$Action chip=$ChipID debugMode=$debugMode clk=$clk operation=0x$('{0:X2}' -f $OperationType)"

    $setRet = Invoke-MrsCall -Name "SetTargetChip" -Call { [MRSNativeLink]::McuCompiler_SetTargetChip($ChipID, $debugMode) }
    Write-MrsInfo "SetTargetChip=$setRet (chip=$ChipID debugMode=$debugMode)"
    if ($setRet -ne 0) {
        throw "McuCompiler_SetTargetChip failed: $setRet"
    }

    # Retry OpenDevice for transient WCH-Link errors (100=open failed, 128=comm failure, 129=timeout)
    $openRet = -1
    for ($attempt = 1; $attempt -le $maxOpenAttempts; $attempt++) {
        $openRet = Invoke-MrsCall -Name "OpenDevice" -Call { [MRSNativeLink]::McuCompiler_OpenDevice() }
        Write-MrsInfo "OpenDevice=$openRet (attempt $attempt/$maxOpenAttempts)"
        if ($openRet -eq 0) { break }
        if ($attempt -lt $maxOpenAttempts) {
            Write-MrsWarn (Get-MRSErrorText $openRet)
            Write-MrsInfo "Waiting 1.5s before retry..."
            Start-Sleep -Seconds 1.5
        }
    }
    if ($openRet -ne 0) {
        throw "Failed to open WCH-Link after $maxOpenAttempts attempts: $openRet"
    }
    $deviceOpened = $true

    $firmwareCfg = Join-Path $FirmwareLinkPath "wchlink.wcfg"
    if (Test-Path -LiteralPath $firmwareCfg) {
        [int]$linkType = 0
        [int]$linkMode = 0
        $compareRet = Invoke-MrsCall -Name "CompareVersion" -Call { [MRSNativeLink]::McuCompiler_CompareVersion($firmwareCfg, [ref]$linkType, [ref]$linkMode) }
        Write-MrsInfo ("CompareVersion=$compareRet linkType=0x{0:X2} linkMode=0x{1:X2}" -f $linkType, $linkMode)
        Write-MrsTrace ("STATE linkType=0x{0:X2} linkMode=0x{1:X2}" -f $linkType, $linkMode)
        if ($compareRet -ne 0) {
            throw "McuCompiler_CompareVersion failed: $compareRet"
        }
    } else {
        Write-MrsWarn "Firmware config not found: $firmwareCfg"
    }

    # SetSDLineMode is only needed for the explicit set-line-mode action; SetTargetChip
    # already programs the link's debug mode for upcoming operations. Calling it on every
    # flash is an extra failure surface — see plan Layer B.2.
    if ($Action -eq "set-line-mode") {
        $lineRet = Invoke-MrsCall -Name "SetSDLineMode" -Call { [MRSNativeLink]::McuCompiler_SetSDLineMode($debugMode) }
        Write-MrsInfo "SetSDLineMode=$lineRet (debugMode=$debugMode)"
        if ($lineRet -ne 0) {
            Write-MrsWarn (Get-MRSErrorText $lineRet)
            throw "SetSDLineMode failed: $lineRet"
        }
    }

    if ($Action -eq "set-target" -or $Action -eq "set-line-mode") {
        exit 0
    }

    if ($Action -eq "dump-link-status" -or $Action -eq "rehandshake") {
        $linkedChip = Invoke-MrsCall -Name "GetLinkedMCUID" -Call { [MRSNativeLink]::MRSFunc_GetLinkedMCUID() }
        Write-MrsInfo "LinkedMCUID=$linkedChip"
        Write-MrsTrace "STATE linkedChip=$linkedChip expected=$ChipID"

        $protect = Invoke-MrsCall -Name "QueryRProtect" -Call { [MRSNativeLink]::MRSFunc_QueryRProtect($ChipID, $clk) }
        Write-MrsInfo "ReadProtect=$protect ($(Get-RProtectText $protect))"
        Write-MrsTrace "STATE readProtect=$protect"

        if ($Action -eq "rehandshake") {
            exit 0
        }
        if ($linkedChip -eq $ChipID -and ($protect -eq 3 -or $protect -eq 4)) {
            exit 0
        }
        exit 1
    }

    if ($Action -eq "reset") {
        $ret = Invoke-MrsCall -Name "ResetB" -Call { [MRSNativeLink]::McuCompiler_ResetB($ChipID, $debugMode) }
        Write-MrsInfo "ResetB=$ret"
        if ($ret -ne 0) {
            Write-MrsWarn (Get-MRSErrorText $ret)
            exit 1
        }
        exit 0
    }

    if ($Action -eq "hold-nrst" -or $Action -eq "release-nrst") {
        $level = if ($Action -eq "hold-nrst") { 0 } else { 1 }
        $ret = Invoke-MrsCall -Name "SetRSTPin($level)" -Call { [MRSNativeLink]::McuCompiler_SetRSTPin($level) }
        Write-MrsInfo "SetRSTPin($level)=$ret"
        if ($ret -ne 0) {
            Write-MrsWarn (Get-MRSErrorText $ret)
            exit 1
        }
        exit 0
    }

    if ($Action -eq "get-chip-id") {
        $linkedChip = Invoke-MrsCall -Name "GetLinkedMCUID" -Call { [MRSNativeLink]::MRSFunc_GetLinkedMCUID() }
        Write-MrsInfo "LinkedMCUID=$linkedChip"
        if ($linkedChip -ne $ChipID) {
            Write-MrsWarn "Linked chip ID does not match expected chip ID $ChipID."
            exit 1
        }
        exit 0
    }

    if ($Action -eq "query-rprotect") {
        $protect = Invoke-MrsCall -Name "QueryRProtect" -Call { [MRSNativeLink]::MRSFunc_QueryRProtect($ChipID, $clk) }
        Write-MrsInfo "ReadProtect=$protect ($(Get-RProtectText $protect))"
        if ($protect -ne 3 -and $protect -ne 4) { exit 1 }
        exit 0
    }

    if ($Action -eq "disable-rprotect") {
        $ret = Invoke-MrsCall -Name "DisableRProtect" -Call { [MRSNativeLink]::MRSFunc_DisableRProtect($ChipID, $clk) }
        Write-MrsInfo "DisableRProtect=$ret"
        if ($ret -ne 0) {
            Write-MrsWarn (Get-MRSErrorText $ret)
            exit 1
        }
        exit 0
    }

    Write-MrsInfo ("FlashOperationExB operation=0x{0:X2} ({1}) address=$FlashAddress file=$DataFilePath" -f $OperationType, (Get-OperationDescription $OperationType))

    # Capture read-protect state before flash so we can detect if the operation flipped it.
    $protectBefore = Invoke-MrsCall -Name "QueryRProtect[pre]" -Call { [MRSNativeLink]::MRSFunc_QueryRProtect($ChipID, $clk) }
    Write-MrsTrace "STATE readProtect[pre]=$protectBefore ($(Get-RProtectText $protectBefore))"

    # Hardware-confirmed pattern (rt-thread project, CH32H417, PB8/PB9 SDI):
    # after the previous flash ended with reset-run, V5F is executing user code
    # at 400 MHz and the chip's SDI does not handshake cleanly on the next
    # flash attempt. QueryRProtect returns 104 (Failed to configure MCU/SWD
    # comm). The fix is the clear-code-flash bit (0x40) in FlashOperationExB:
    # the DLL uses a more aggressive entry sequence (NRST pulse + option-byte
    # path) that succeeds even when the CPU is busy. Confirmed by -Action
    # recover, which uses op 0x4C against a 4-byte stub and always recovers.
    $effectiveOperation = $OperationType
    if ($protectBefore -eq 104) {
        Write-MrsWarn "QueryRProtect[pre]=104 (chip busy / SDI not responding). OR-ing clear-code-flash (0x40) into op for forced entry."
        $effectiveOperation = $OperationType -bor 0x40
        Write-MrsInfo ("Effective operation: 0x{0:X2} ({1})" -f $effectiveOperation, (Get-OperationDescription $effectiveOperation))
    }

    # Retry FlashOperationExB. Error 104 is the chip-busy signature on this
    # hardware; on retry we OR clear-code-flash into the operation byte to
    # trigger the DLL's aggressive entry sequence.
    $flashRet = -1
    for ($attempt = 1; $attempt -le $maxFlashAttempts; $attempt++) {
        $flashRet = Invoke-MrsCall -Name "FlashOperationExB[$attempt]" -Call { [MRSNativeLink]::MRSFunc_FlashOperationExB($ChipID, $clk, $effectiveOperation, $flashAddr, $DataFilePath) }
        Write-MrsInfo ("FlashOperationExB=$flashRet (attempt $attempt/$maxFlashAttempts, op=0x{0:X2})" -f $effectiveOperation)
        if ($flashRet -eq 0) { break }

        $isRetryable = ($flashRet -eq 100 -or $flashRet -eq 104 -or $flashRet -eq 128 -or $flashRet -eq 129 -or $flashRet -eq 110)
        if ($isRetryable -and $attempt -lt $maxFlashAttempts) {
            Write-MrsWarn (Get-MRSErrorText $flashRet)
            if ($flashRet -eq 104 -and (($effectiveOperation -band 0x40) -eq 0)) {
                $effectiveOperation = $effectiveOperation -bor 0x40
                Write-MrsInfo ("Escalating to op 0x{0:X2} (added clear-code-flash) before retry." -f $effectiveOperation)
            }
            Write-MrsInfo "Waiting 1.5s before retry..."
            Start-Sleep -Seconds 1.5
        } else {
            break
        }
    }

    # Re-check protection state after flash; surface any unexpected transition.
    $protectAfter = Invoke-MrsCall -Name "QueryRProtect[post]" -Call { [MRSNativeLink]::MRSFunc_QueryRProtect($ChipID, $clk) }
    Write-MrsTrace "STATE readProtect[post]=$protectAfter ($(Get-RProtectText $protectAfter))"
    if ($protectBefore -ne $protectAfter) {
        Write-MrsWarn ("ReadProtect changed during flash: {0} -> {1}" -f (Get-RProtectText $protectBefore), (Get-RProtectText $protectAfter))
    }

    if ($flashRet -ne 0) {
        Write-MrsWarn (Get-MRSErrorText $flashRet)
        exit 1
    }

    if (($OperationType -band 0x20) -ne 0) { Write-MrsInfo "Disable Code-Protect finished" }
    if (($OperationType -band 0x08) -ne 0) { Write-MrsInfo "Erase finished" }
    if (($OperationType -band 0x04) -ne 0) { Write-MrsInfo "Program finished" }
    if (($OperationType -band 0x02) -ne 0) { Write-MrsInfo "Verify finished" }
    if (($OperationType -band 0x01) -ne 0) { Write-MrsInfo "Reset finished" }
    exit 0
} finally {
    if ($deviceOpened) {
        $closeRet = Invoke-MrsCall -Name "CloseDevice" -Call { [MRSNativeLink]::McuCompiler_CloseDevice() }
        Write-MrsInfo "CloseDevice=$closeRet"
    }
    if ($oldTmp) { $env:TMP = $oldTmp }
    if ($oldTemp) { $env:TEMP = $oldTemp }
    Write-MrsTrace "EXIT action=$Action"
}
