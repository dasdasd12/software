#requires -Version 5.1
<#
.SYNOPSIS
    WCH MounRiver Studio command-line build, flash, and debug helper.

.EXAMPLE
    .\wch-auto.ps1 -Action detect -ProjectDir C:\work\project
    .\wch-auto.ps1 -Action init -ProjectDir C:\program1\Program\H417lib\HSEM_CoreSync -Chip CH32H417
    .\wch-auto.ps1 -Action build -ProjectDir C:\program1\Program\H417lib\HSEM_CoreSync -Chip CH32H417 -Core both
    .\wch-auto.ps1 -Action flash -ProjectDir C:\program1\Program\H417lib\HSEM_CoreSync -Chip CH32H417 -Core both
    .\wch-auto.ps1 -Action debug-check -ProjectDir C:\program1\Program\H417lib\HSEM_CoreSync -Chip CH32H417 -Core v3f
#>
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("detect", "init", "build", "flash", "debug", "debug-check", "loop")]
    [string]$Action,

    [string]$ProjectDir = (Get-Location).Path,

    [string]$ElfPath = "",
    [string]$ElfPathV3F = "",
    [string]$ElfPathV5F = "",

    [ValidateSet("auto", "CH32H417", "CH585", "CH32V203", "CH32V003", "CH32X035")]
    [string]$Chip = "auto",

    [ValidateSet("auto", "v3f", "v5f", "both")]
    [string]$Core = "auto",

    [string]$MRSPath = "C:\MounRiver\MounRiver_Studio2",
    [string]$EVTRoot = "C:\program1\hardware\WCH\CH32H417\CH32H417EVT\EVT\EXAM",
    [int]$GdbPort = 0,

    [switch]$SkipBuild,
    [switch]$SkipFlash,
    [switch]$VisibleOpenOCD,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

function Write-Info { param([string]$Message) Write-Host "[INFO]  $Message" -ForegroundColor Cyan }
function Write-Ok   { param([string]$Message) Write-Host "[OK]    $Message" -ForegroundColor Green }
function Write-Warn { param([string]$Message) Write-Host "[WARN]  $Message" -ForegroundColor Yellow }
function Write-Err  { param([string]$Message) Write-Host "[ERR]   $Message" -ForegroundColor Red }
function Write-Step { param([string]$Message) Write-Host "`n========== $Message ==========" -ForegroundColor Cyan }

function Convert-ToForwardPath {
    param([string]$Path)
    return ($Path -replace "\\", "/")
}

function Resolve-ExistingPath {
    param([string]$Path)
    if (Test-Path -LiteralPath $Path) {
        return (Resolve-Path -LiteralPath $Path).Path
    }
    return [System.IO.Path]::GetFullPath($Path)
}

function Get-UniqueExistingDirs {
    param([string[]]$Dirs)
    $result = New-Object System.Collections.Generic.List[string]
    foreach ($dir in $Dirs) {
        if (-not $dir) { continue }
        if ((Test-Path -LiteralPath $dir) -and (-not $result.Contains($dir))) {
            [void]$result.Add($dir)
        }
    }
    return $result.ToArray()
}

function Find-MRSToolchain {
    param([string]$BasePath)

    $roots = @(
        $BasePath,
        "C:\MounRiver\MounRiver_Studio2",
        "C:\MounRiver\MounRiver_Studio",
        "C:\Program Files\MounRiver_Studio2",
        "C:\Program Files (x86)\MounRiver_Studio2"
    )

    foreach ($root in $roots) {
        if (-not $root) { continue }

        $openocd = Join-Path $root "resources\app\resources\win32\components\WCH\OpenOCD\OpenOCD\bin\openocd.exe"
        $openocdBin = Split-Path $openocd -Parent
        $make = Join-Path $root "resources\app\resources\win32\others\Build_Tools\Make\bin\make.exe"

        $gcc12Bin = Join-Path $root "resources\app\resources\win32\components\WCH\Toolchain\RISC-V Embedded GCC12\bin"
        $gcc15Bin = Join-Path $root "resources\app\resources\win32\components\WCH\Toolchain\RISC-V Embedded GCC15\bin"
        $gcc12 = Join-Path $gcc12Bin "riscv-wch-elf-gcc.exe"
        $gdb12 = Join-Path $gcc12Bin "riscv-wch-elf-gdb.exe"
        $gcc15 = Join-Path $gcc15Bin "riscv32-wch-elf-gcc.exe"
        $gdb15 = Join-Path $gcc15Bin "riscv32-wch-elf-gdb.exe"

        if (-not (Test-Path -LiteralPath $openocd)) { continue }

        $gcc = $null
        $gdb = $null
        $prefix = $null
        $primaryBin = $null

        # WCH EVT projects in this workspace use GCC12 names in generated makefiles.
        if (Test-Path -LiteralPath $gcc12) {
            $gcc = $gcc12
            $gdb = $gdb12
            $prefix = "riscv-wch-elf-"
            $primaryBin = $gcc12Bin
        } elseif (Test-Path -LiteralPath $gcc15) {
            $gcc = $gcc15
            $gdb = $gdb15
            $prefix = "riscv32-wch-elf-"
            $primaryBin = $gcc15Bin
        }

        $pathDirs = Get-UniqueExistingDirs @(
            (Split-Path $make -Parent),
            $gcc12Bin,
            $gcc15Bin,
            $primaryBin,
            $openocdBin
        )

        return @{
            MRSPath = $root
            OpenOCD = $openocd
            OpenOCDBin = $openocdBin
            Make = if (Test-Path -LiteralPath $make) { $make } else { "make" }
            GCC = $gcc
            GDB = $gdb
            Prefix = $prefix
            GCCBin = $primaryBin
            PathDirs = $pathDirs
            WCHLinkUpdateTool = Join-Path $root "resources\app\resources\win32\components\WCH\Others\WCHLinkEJtagUpdTool\default\WCHLinkEJtagUpdTool.exe"
        }
    }

    return $null
}

function Add-ToolchainPath {
    param([hashtable]$Toolchain)
    if (-not $Toolchain) { return }
    $prefix = ($Toolchain.PathDirs | Where-Object { $_ }) -join ";"
    if ($prefix) {
        $env:PATH = "$prefix;$env:PATH"
    }
}

function Get-WCHLinkDevices {
    try {
        return Get-PnpDevice -ErrorAction Stop | Where-Object {
            $_.InstanceId -match "VID_1A86.*PID_801[012]" -or
            $_.FriendlyName -like "*WCH-Link*" -or
            $_.FriendlyName -like "*CMSIS-DAP*"
        }
    } catch {
        return @()
    }
}

function Get-WCHLinkMode {
    $devices = @(Get-WCHLinkDevices)
    if ($devices.Count -eq 0) { return "none" }
    if ($devices | Where-Object { $_.FriendlyName -like "*CMSIS-DAP*" }) { return "cmsis-dap-or-riscv" }
    if ($devices | Where-Object { $_.FriendlyName -like "*WCH-Link*" }) { return "wch-link" }
    return "unknown"
}

function Infer-Chip {
    param([string]$Dir, [string]$RequestedChip)
    if ($RequestedChip -ne "auto") { return $RequestedChip }
    if ($Dir -match "H417|CH32H417") { return "CH32H417" }
    if ($Dir -match "CH585|CH584|585|584") { return "CH585" }
    if ($Dir -match "V203|CH32V203") { return "CH32V203" }
    return "CH32H417"
}

function Get-H417ProjectRoot {
    param([string]$Dir)
    $full = Resolve-ExistingPath $Dir
    $leaf = Split-Path $full -Leaf
    if (($leaf -ieq "V3F" -or $leaf -ieq "V5F") -and (Test-Path -LiteralPath (Join-Path (Split-Path $full -Parent) "Common"))) {
        return (Split-Path $full -Parent)
    }
    return $full
}

function Test-H417DualLayout {
    param([string]$Dir)
    $root = Get-H417ProjectRoot $Dir
    return (
        (Test-Path -LiteralPath (Join-Path $root "V3F\User")) -and
        (Test-Path -LiteralPath (Join-Path $root "V5F\User"))
    )
}

function Get-H417CoreInfo {
    param([ValidateSet("v3f", "v5f")] [string]$CoreName)
    if ($CoreName -eq "v3f") {
        return @{
            Name = "v3f"
            Upper = "V3F"
            Target = "wch_riscv.cpu.0"
            GdbPort = 3333
            Startup = "startup_ch32h417_v3f.S"
            Linker = "Link_v3f.ld"
            FlashOrigin = "0x00000000"
        }
    }
    return @{
        Name = "v5f"
        Upper = "V5F"
        Target = "wch_riscv.cpu.1"
        GdbPort = 3334
        Startup = "startup_ch32h417_v5f.S"
        Linker = "Link_v5f.ld"
        FlashOrigin = "0x00010000"
    }
}

function Resolve-Core {
    param([string]$Dir, [string]$RequestedCore, [string]$CurrentChip, [string]$Elf)
    if ($CurrentChip -ne "CH32H417") { return "single" }
    if ($RequestedCore -ne "auto") { return $RequestedCore }
    if ($Elf -match "V5F|v5f") { return "v5f" }
    if ($Elf -match "V3F|v3f") { return "v3f" }

    $leaf = Split-Path (Resolve-ExistingPath $Dir) -Leaf
    if ($leaf -ieq "V3F") { return "v3f" }
    if ($leaf -ieq "V5F") { return "v5f" }
    if (Test-H417DualLayout $Dir) { return "both" }
    return "v3f"
}

function Test-MRSProjectStructure {
    param([string]$Dir)
    if (Test-Path -LiteralPath (Join-Path $Dir "Makefile")) { return $true }
    if (Test-H417DualLayout $Dir) { return $true }
    $projectFiles = @(
        (Get-ChildItem -Path $Dir -Filter "*.wvproj" -ErrorAction SilentlyContinue),
        (Get-ChildItem -Path $Dir -Filter "*.wvsln" -ErrorAction SilentlyContinue),
        (Get-ChildItem -Path $Dir -Filter ".cproject" -ErrorAction SilentlyContinue),
        (Get-ChildItem -Path $Dir -Filter ".project" -ErrorAction SilentlyContinue)
    )
    return (($projectFiles | Where-Object { $_ }).Count -gt 0)
}

function Find-ElfFile {
    param([string]$Dir, [string]$CoreName = "single")
    $root = Get-H417ProjectRoot $Dir
    $patterns = New-Object System.Collections.Generic.List[string]

    if ($CoreName -eq "v3f") {
        [void]$patterns.Add((Join-Path $root "build\v3f\*.elf"))
        [void]$patterns.Add((Join-Path $root "V3F\obj\*V3F*.elf"))
        [void]$patterns.Add((Join-Path $root "V3F\obj\*.elf"))
    } elseif ($CoreName -eq "v5f") {
        [void]$patterns.Add((Join-Path $root "build\v5f\*.elf"))
        [void]$patterns.Add((Join-Path $root "V5F\obj\*V5F*.elf"))
        [void]$patterns.Add((Join-Path $root "V5F\obj\*.elf"))
    }

    [void]$patterns.Add((Join-Path $Dir "build\*.elf"))
    [void]$patterns.Add((Join-Path $Dir "obj\*.elf"))
    [void]$patterns.Add((Join-Path $Dir "*.elf"))
    [void]$patterns.Add((Join-Path $Dir "Debug\*.elf"))
    [void]$patterns.Add((Join-Path $Dir "Release\*.elf"))

    foreach ($pattern in $patterns) {
        $files = @(Get-ChildItem -Path $pattern -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending)
        if ($files.Count -gt 0) { return $files[0].FullName }
    }
    return $null
}

function Find-H417ElfPair {
    param([string]$Dir)
    $v3 = if ($ElfPathV3F) { Resolve-ExistingPath $ElfPathV3F } else { Find-ElfFile -Dir $Dir -CoreName "v3f" }
    $v5 = if ($ElfPathV5F) { Resolve-ExistingPath $ElfPathV5F } else { Find-ElfFile -Dir $Dir -CoreName "v5f" }
    return @{ v3f = $v3; v5f = $v5 }
}

function New-H417DualMakefile {
    param([string]$Root, [string]$EvtRoot)
    $makefile = Join-Path $Root "Makefile"
    if (Test-Path -LiteralPath $makefile) {
        Write-Warn "Makefile already exists, leaving it unchanged: $makefile"
        return
    }

    $scriptDir = Split-Path -Parent $PSCommandPath
    $template = Join-Path $scriptDir "Makefile.h417-dual.template"
    if (-not (Test-Path -LiteralPath $template)) {
        throw "Missing template: $template"
    }
    if (-not (Test-Path -LiteralPath $EvtRoot)) {
        throw "EVT root not found: $EvtRoot"
    }

    $projectName = Split-Path $Root -Leaf
    $content = Get-Content -LiteralPath $template -Raw -Encoding UTF8
    $content = $content.Replace("{{PROJECT_NAME}}", $projectName)
    $content = $content.Replace("{{EVT_ROOT}}", (Convert-ToForwardPath (Resolve-ExistingPath $EvtRoot)))
    Set-Content -LiteralPath $makefile -Value $content -Encoding UTF8
    Write-Ok "Generated CH32H417 dual-core Makefile: $makefile"
}

function New-GenericMakefile {
    param([string]$Dir, [string]$TargetChip)
    $makefile = Join-Path $Dir "Makefile"
    if (Test-Path -LiteralPath $makefile) {
        Write-Warn "Makefile already exists, leaving it unchanged: $makefile"
        return
    }

    $srcDirs = @()
    foreach ($name in @("src", "Src", "Core", "Startup", "Lib", "User", "Common")) {
        if (Test-Path -LiteralPath (Join-Path $Dir $name)) { $srcDirs += $name }
    }
    if ($srcDirs.Count -eq 0) { $srcDirs = @(".") }

    $ldItem = Get-ChildItem -Path $Dir -Filter "*.ld" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
    $ldScript = if ($ldItem) { Convert-ToForwardPath (Resolve-Path -LiteralPath $ldItem.FullName -Relative) } else { "Link.ld" }

    $scriptDir = Split-Path -Parent $PSCommandPath
    $template = Join-Path $scriptDir "Makefile.template"
    $content = Get-Content -LiteralPath $template -Raw -Encoding UTF8
    $content = $content.Replace("{{CHIP}}", $TargetChip)
    $content = $content.Replace("{{TIMESTAMP}}", (Get-Date -Format "yyyy-MM-dd HH:mm:ss"))
    $content = $content.Replace("{{SRC_DIRS}}", ($srcDirs -join " "))
    $content = $content.Replace("{{LDSCRIPT}}", $ldScript)
    Set-Content -LiteralPath $makefile -Value $content -Encoding UTF8
    Write-Ok "Generated Makefile: $makefile"
}

function New-VSCodeConfig {
    param([string]$Root, [string]$TargetChip, [hashtable]$Toolchain)
    $vscodeDir = Join-Path $Root ".vscode"
    if (-not (Test-Path -LiteralPath $vscodeDir)) {
        New-Item -ItemType Directory -Path $vscodeDir | Out-Null
    }

    $scriptDir = Split-Path -Parent $PSCommandPath
    $scriptPath = Convert-ToForwardPath (Resolve-ExistingPath $PSCommandPath)
    $tasksTemplate = Join-Path $scriptDir "tasks.json.template"
    $launchTemplate = Join-Path $scriptDir "launch.json.template"

    if (Test-Path -LiteralPath $tasksTemplate) {
        $tasks = Get-Content -LiteralPath $tasksTemplate -Raw -Encoding UTF8
        $tasks = $tasks.Replace("{{CHIP}}", $TargetChip)
        $tasks = $tasks.Replace("{{SCRIPT}}", $scriptPath)
        Set-Content -LiteralPath (Join-Path $vscodeDir "tasks.json") -Value $tasks -Encoding UTF8
        Write-Ok "Generated .vscode/tasks.json"
    }

    if (Test-Path -LiteralPath $launchTemplate) {
        $launch = Get-Content -LiteralPath $launchTemplate -Raw -Encoding UTF8
        $cfgFile = if ($TargetChip -eq "CH32H417") { "wch-dual-core.cfg" } else { "wch-riscv.cfg" }
        $gdbExe = if ($Toolchain.GDB) { Split-Path $Toolchain.GDB -Leaf } else { "riscv-wch-elf-gdb.exe" }
        $launch = $launch.Replace("{{CHIP}}", $TargetChip)
        $launch = $launch.Replace("{{GDBEXE}}", $gdbExe)
        $launch = $launch.Replace("{{CFGFILE}}", $cfgFile)
        Set-Content -LiteralPath (Join-Path $vscodeDir "launch.json") -Value $launch -Encoding UTF8
        Write-Ok "Generated .vscode/launch.json"
    }
}

function Invoke-Detect {
    param([hashtable]$Toolchain, [string]$TargetChip)
    Write-Step "WCH environment"
    if (-not $Toolchain) {
        Write-Err "MounRiver Studio 2 toolchain was not found."
        return
    }

    Write-Ok "MRS2:     $($Toolchain.MRSPath)"
    Write-Ok "OpenOCD:  $($Toolchain.OpenOCD)"
    Write-Ok "Make:     $($Toolchain.Make)"
    Write-Ok "GCC:      $($Toolchain.GCC)"
    Write-Ok "GDB:      $($Toolchain.GDB)"
    Write-Info "WCH-Link mode hint: $(Get-WCHLinkMode)"

    if ($TargetChip -eq "CH32H417") {
        Write-Step "CH32H417 mapping"
        Write-Host "V3F: target wch_riscv.cpu.0, GDB 3333, flash origin 0x00000000" -ForegroundColor White
        Write-Host "V5F: target wch_riscv.cpu.1, GDB 3334, flash origin 0x00010000" -ForegroundColor White
        Write-Host "Dual projects should flash V3F first, then V5F." -ForegroundColor White
    }

    $root = if ($TargetChip -eq "CH32H417") { Get-H417ProjectRoot $ProjectDir } else { Resolve-ExistingPath $ProjectDir }
    if (Test-Path -LiteralPath (Join-Path $root "Makefile")) {
        Write-Ok "Project Makefile: $(Join-Path $root "Makefile")"
    } elseif (Test-MRSProjectStructure $root) {
        Write-Warn "No root Makefile found, but an MRS/EVT structure was detected."
        Write-Info "Run init to generate command-line build files."
    } else {
        Write-Warn "No Makefile or known MRS structure detected."
    }

    foreach ($coreName in @("v3f", "v5f")) {
        if ($TargetChip -eq "CH32H417") {
            $elf = Find-ElfFile -Dir $root -CoreName $coreName
            if ($elf) { Write-Ok "$($coreName.ToUpper()) ELF: $elf" }
        }
    }
}

function Invoke-Init {
    param([hashtable]$Toolchain, [string]$TargetChip)
    Write-Step "Project init"
    $root = if ($TargetChip -eq "CH32H417") { Get-H417ProjectRoot $ProjectDir } else { Resolve-ExistingPath $ProjectDir }

    if ($TargetChip -eq "CH32H417" -and (Test-H417DualLayout $root)) {
        New-H417DualMakefile -Root $root -EvtRoot $EVTRoot
    } else {
        New-GenericMakefile -Dir $root -TargetChip $TargetChip
    }
    New-VSCodeConfig -Root $root -TargetChip $TargetChip -Toolchain $Toolchain
}

function Invoke-MakeAt {
    param([hashtable]$Toolchain, [string]$Dir, [string[]]$Args)
    Push-Location $Dir
    try {
        Add-ToolchainPath $Toolchain
        & $Toolchain.Make @Args
        if ($LASTEXITCODE -ne 0) { throw "make failed in $Dir with exit code $LASTEXITCODE" }
    } finally {
        Pop-Location
    }
}

function Invoke-Build {
    param([hashtable]$Toolchain, [string]$TargetChip, [string]$RequestedCore)
    Write-Step "Build"
    if (-not $Toolchain) { throw "MRS toolchain not found." }

    $root = if ($TargetChip -eq "CH32H417") { Get-H417ProjectRoot $ProjectDir } else { Resolve-ExistingPath $ProjectDir }
    $resolvedCore = Resolve-Core -Dir $root -RequestedCore $RequestedCore -CurrentChip $TargetChip -Elf $ElfPath

    if (-not (Test-Path -LiteralPath (Join-Path $root "Makefile"))) {
        if (Test-MRSProjectStructure $root) {
            Invoke-Init -Toolchain $Toolchain -TargetChip $TargetChip
        } else {
            throw "No Makefile and no supported MRS structure found in $root"
        }
    }

    if (Test-Path -LiteralPath (Join-Path $root "Makefile")) {
        $coreArg = if ($resolvedCore -eq "single") { "CORE=single" } else { "CORE=$resolvedCore" }
        Write-Info "make -j $coreArg PREFIX=$($Toolchain.Prefix)"
        Invoke-MakeAt -Toolchain $Toolchain -Dir $root -Args @("-j", "CHIP=$TargetChip", $coreArg, "PREFIX=$($Toolchain.Prefix)")
        Write-Ok "Build finished"
        return
    }

    throw "Build setup failed for $root"
}

function Invoke-OpenOCD {
    param([hashtable]$Toolchain, [string[]]$OpenOCDArgs)
    Write-Info "openocd $($OpenOCDArgs -join ' ')"
    if ($DryRun) {
        Write-Warn "Dry run: OpenOCD was not executed."
        return
    }
    & $Toolchain.OpenOCD @OpenOCDArgs
    if ($LASTEXITCODE -ne 0) { throw "OpenOCD failed with exit code $LASTEXITCODE" }
}

function Invoke-Flash {
    param([hashtable]$Toolchain, [string]$TargetChip, [string]$RequestedCore)
    Write-Step "Flash"
    if (-not $Toolchain) { throw "MRS toolchain not found." }

    $root = if ($TargetChip -eq "CH32H417") { Get-H417ProjectRoot $ProjectDir } else { Resolve-ExistingPath $ProjectDir }
    $resolvedCore = Resolve-Core -Dir $root -RequestedCore $RequestedCore -CurrentChip $TargetChip -Elf $ElfPath

    if ($TargetChip -eq "CH32H417") {
        $baseArgs = @("-s", $Toolchain.OpenOCDBin, "-f", "wch-dual-core.cfg", "-c", "init")

        if ($resolvedCore -eq "both") {
            $pair = Find-H417ElfPair -Dir $root
            if (-not $pair.v3f -or -not $pair.v5f) {
                throw "Both-core flash needs V3F and V5F ELF files. Build first or pass -ElfPathV3F and -ElfPathV5F."
            }

            $v3 = Convert-ToForwardPath $pair.v3f
            $v5 = Convert-ToForwardPath $pair.v5f
            $args = $baseArgs + @(
                "-c", "targets wch_riscv.cpu.0",
                "-c", "halt",
                "-c", "program `"$v3`" verify",
                "-c", "targets wch_riscv.cpu.1",
                "-c", "halt",
                "-c", "program `"$v5`" verify",
                "-c", "reset run",
                "-c", "exit"
            )
            Write-Info "V3F ELF: $($pair.v3f)"
            Write-Info "V5F ELF: $($pair.v5f)"
            Invoke-OpenOCD -Toolchain $Toolchain -OpenOCDArgs $args
            Write-Ok "Dual-core flash finished"
            return
        }

        $info = Get-H417CoreInfo $resolvedCore
        $elf = if ($ElfPath) { Resolve-ExistingPath $ElfPath } else { Find-ElfFile -Dir $root -CoreName $resolvedCore }
        if (-not $elf) { throw "ELF not found for $resolvedCore. Build first or pass -ElfPath." }
        $elfForward = Convert-ToForwardPath $elf
        $programCmd = if ($resolvedCore -eq "v3f") { "program `"$elfForward`" verify reset" } else { "program `"$elfForward`" verify" }
        $args = $baseArgs + @(
            "-c", "targets $($info.Target)",
            "-c", "halt",
            "-c", $programCmd,
            "-c", "exit"
        )
        Write-Info "$($info.Upper) ELF: $elf"
        if ($resolvedCore -eq "v5f") {
            Write-Warn "V5F-only flash will not normally run after reset unless a V3F image wakes V5F."
        }
        Invoke-OpenOCD -Toolchain $Toolchain -OpenOCDArgs $args
        Write-Ok "Flash finished"
        return
    }

    $elfSingle = if ($ElfPath) { Resolve-ExistingPath $ElfPath } else { Find-ElfFile -Dir $root -CoreName "single" }
    if (-not $elfSingle) { throw "ELF not found. Build first or pass -ElfPath." }
    $singleForward = Convert-ToForwardPath $elfSingle
    Invoke-OpenOCD -Toolchain $Toolchain -OpenOCDArgs @(
        "-s", $Toolchain.OpenOCDBin,
        "-f", "wch-riscv.cfg",
        "-c", "init",
        "-c", "halt",
        "-c", "program `"$singleForward`" verify reset",
        "-c", "exit"
    )
    Write-Ok "Flash finished"
}

function Invoke-Debug {
    param([hashtable]$Toolchain, [string]$TargetChip, [string]$RequestedCore, [bool]$CheckOnly)
    Write-Step $(if ($CheckOnly) { "Debug check" } else { "Interactive debug" })
    if (-not $Toolchain) { throw "MRS toolchain not found." }
    if (-not $Toolchain.GDB) { throw "GDB not found in the MRS toolchain." }

    $root = if ($TargetChip -eq "CH32H417") { Get-H417ProjectRoot $ProjectDir } else { Resolve-ExistingPath $ProjectDir }
    $resolvedCore = Resolve-Core -Dir $root -RequestedCore $RequestedCore -CurrentChip $TargetChip -Elf $ElfPath
    if ($resolvedCore -eq "both") { throw "Debug one core at a time: use -Core v3f or -Core v5f." }

    $cfgFile = if ($TargetChip -eq "CH32H417") { "wch-dual-core.cfg" } else { "wch-riscv.cfg" }
    $port = 3333
    $elfCore = "single"
    if ($TargetChip -eq "CH32H417") {
        $info = Get-H417CoreInfo $resolvedCore
        $port = $info.GdbPort
        $elfCore = $resolvedCore
    }
    if ($GdbPort -gt 0) { $port = $GdbPort }

    $elf = if ($ElfPath) { Resolve-ExistingPath $ElfPath } else { Find-ElfFile -Dir $root -CoreName $elfCore }
    if (-not $elf) { throw "ELF not found. Build first or pass -ElfPath." }

    $windowStyle = if ($VisibleOpenOCD) { "Normal" } else { "Hidden" }
    $ocdArgs = @("-s", $Toolchain.OpenOCDBin, "-f", $cfgFile)
    Write-Info "Starting OpenOCD on $cfgFile"
    if ($DryRun) {
        Write-Warn "Dry run: OpenOCD/GDB were not executed."
        Write-Info "OpenOCD args: $($ocdArgs -join ' ')"
        Write-Info "GDB port: $port"
        Write-Info "ELF: $elf"
        return
    }
    $ocdProc = Start-Process -FilePath $Toolchain.OpenOCD -ArgumentList $ocdArgs -PassThru -WindowStyle $windowStyle

    try {
        Start-Sleep -Seconds 2
        Add-ToolchainPath $Toolchain

        if ($CheckOnly) {
            $haltCommand = "monitor reset halt"
            if ($TargetChip -eq "CH32H417" -and $resolvedCore -eq "v5f") {
                # V5F is normally woken by V3F. Resetting here can put it back into
                # the pre-wakeup state, so only halt the already-running secondary core.
                $haltCommand = "monitor halt"
            }
            $gdbArgs = @(
                "-batch",
                $elf,
                "-ex", "set mem inaccessible-by-default off",
                "-ex", "set architecture riscv:rv32",
                "-ex", "set remotetimeout 30",
                "-ex", "set disassembler-options xw",
                "-ex", "target remote localhost:$port",
                "-ex", $haltCommand,
                "-ex", "info registers pc sp gp",
                "-ex", "detach",
                "-ex", "quit"
            )
        } else {
            $haltCommand = "monitor reset halt"
            if ($TargetChip -eq "CH32H417" -and $resolvedCore -eq "v5f") {
                $haltCommand = "monitor halt"
            }
            $gdbArgs = @(
                $elf,
                "-ex", "set mem inaccessible-by-default off",
                "-ex", "set architecture riscv:rv32",
                "-ex", "set remotetimeout unlimited",
                "-ex", "set disassembler-options xw",
                "-ex", "target remote localhost:$port",
                "-ex", $haltCommand,
                "-ex", "load",
                "-ex", "break main"
            )
        }

        Write-Info "GDB port: $port"
        & $Toolchain.GDB @gdbArgs
        if ($LASTEXITCODE -ne 0) { throw "GDB failed with exit code $LASTEXITCODE" }
    } finally {
        if ($ocdProc -and -not $ocdProc.HasExited) {
            Stop-Process -Id $ocdProc.Id -Force -ErrorAction SilentlyContinue
        }
    }
}

$ProjectDir = Resolve-ExistingPath $ProjectDir
$Chip = Infer-Chip -Dir $ProjectDir -RequestedChip $Chip
$toolchain = Find-MRSToolchain -BasePath $MRSPath

switch ($Action) {
    "detect" {
        Invoke-Detect -Toolchain $toolchain -TargetChip $Chip
    }
    "init" {
        Invoke-Init -Toolchain $toolchain -TargetChip $Chip
    }
    "build" {
        Invoke-Build -Toolchain $toolchain -TargetChip $Chip -RequestedCore $Core
    }
    "flash" {
        Invoke-Flash -Toolchain $toolchain -TargetChip $Chip -RequestedCore $Core
    }
    "debug" {
        Invoke-Debug -Toolchain $toolchain -TargetChip $Chip -RequestedCore $Core -CheckOnly:$false
    }
    "debug-check" {
        Invoke-Debug -Toolchain $toolchain -TargetChip $Chip -RequestedCore $Core -CheckOnly:$true
    }
    "loop" {
        if (-not $SkipBuild) {
            Invoke-Build -Toolchain $toolchain -TargetChip $Chip -RequestedCore $Core
        }
        if (-not $SkipFlash) {
            Invoke-Flash -Toolchain $toolchain -TargetChip $Chip -RequestedCore $Core
        }
        Write-Host ""
        Write-Info "For noninteractive debug validation:"
        Write-Host "  .\wch-auto.ps1 -Action debug-check -ProjectDir `"$ProjectDir`" -Chip $Chip -Core v3f" -ForegroundColor White
        if ($Chip -eq "CH32H417") {
            Write-Host "  .\wch-auto.ps1 -Action debug-check -ProjectDir `"$ProjectDir`" -Chip $Chip -Core v5f" -ForegroundColor White
        }
    }
}
