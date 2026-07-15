[CmdletBinding()]
param(
    [string]$Python,
    [string]$TargetTriple = "x86_64-pc-windows-msvc",
    [switch]$SkipDependencyInstall
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ($env:OS -ne "Windows_NT") {
    throw "The current sidecar target is Windows x64; run this script on Windows."
}
if ($TargetTriple -ne "x86_64-pc-windows-msvc") {
    throw "Unsupported target triple '$TargetTriple'. Only x86_64-pc-windows-msvc is packaged today."
}

$desktopRoot = Split-Path -Parent $PSScriptRoot
$softwareRoot = Split-Path -Parent $desktopRoot
$sourceRoot = Join-Path $softwareRoot "src"
$factoryProfile = Join-Path $softwareRoot "config\factory_default_profile.json"
$entryPoint = Join-Path $PSScriptRoot "kiiie_core_entry.py"
$requirements = Join-Path $PSScriptRoot "sidecar-requirements.txt"
$buildRoot = Join-Path $desktopRoot ".sidecar-build"
$venvRoot = Join-Path $buildRoot "venv"
$venvPython = Join-Path $venvRoot "Scripts\python.exe"
$workPath = Join-Path $buildRoot "work"
$distPath = Join-Path $buildRoot "dist"
$specPath = Join-Path $buildRoot "spec"
$binaryDirectory = Join-Path $desktopRoot "src-tauri\binaries"
$targetBinary = Join-Path $binaryDirectory "kiiie-core-$TargetTriple.exe"

foreach ($requiredPath in @($sourceRoot, $factoryProfile, $entryPoint, $requirements)) {
    if (-not (Test-Path -LiteralPath $requiredPath)) {
        throw "Required sidecar input was not found: $requiredPath"
    }
}

function Resolve-BootstrapPython {
    if ($Python) {
        $command = Get-Command $Python -ErrorAction SilentlyContinue
        if ($command) {
            return @{ Executable = $command.Source; Prefix = @() }
        }
        if (Test-Path -LiteralPath $Python) {
            return @{ Executable = (Resolve-Path -LiteralPath $Python).Path; Prefix = @() }
        }
        throw "The requested Python executable was not found: $Python"
    }

    if ($env:KIIIE_SIDECAR_PYTHON) {
        $command = Get-Command $env:KIIIE_SIDECAR_PYTHON -ErrorAction SilentlyContinue
        if ($command) {
            return @{ Executable = $command.Source; Prefix = @() }
        }
        if (Test-Path -LiteralPath $env:KIIIE_SIDECAR_PYTHON) {
            return @{ Executable = (Resolve-Path -LiteralPath $env:KIIIE_SIDECAR_PYTHON).Path; Prefix = @() }
        }
        throw "KIIIE_SIDECAR_PYTHON does not point to an executable: $env:KIIIE_SIDECAR_PYTHON"
    }

    $projectPython = Join-Path $softwareRoot ".venv-claude\Scripts\python.exe"
    if (Test-Path -LiteralPath $projectPython) {
        return @{ Executable = $projectPython; Prefix = @() }
    }

    $pyLauncher = Get-Command "py" -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        return @{ Executable = $pyLauncher.Source; Prefix = @("-3.11") }
    }

    $pathPython = Get-Command "python" -ErrorAction SilentlyContinue
    if ($pathPython) {
        return @{ Executable = $pathPython.Source; Prefix = @() }
    }

    throw "Python 3.11+ was not found. Set KIIIE_SIDECAR_PYTHON or pass -Python."
}

function Invoke-BootstrapPython {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    $executable = $script:bootstrapPython.Executable
    $prefix = @($script:bootstrapPython.Prefix)
    $oldPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $executable @prefix @Arguments
    $exitCode = $LASTEXITCODE
    $ErrorActionPreference = $oldPreference
    if ($exitCode -ne 0) {
        throw "Bootstrap Python failed with exit code $exitCode."
    }
}

$bootstrapPython = Resolve-BootstrapPython
$bootstrapExecutable = $bootstrapPython.Executable
$bootstrapPrefix = @($bootstrapPython.Prefix)
$oldPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$versionText = & $bootstrapExecutable @bootstrapPrefix -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')"
$versionExitCode = $LASTEXITCODE
$ErrorActionPreference = $oldPreference
if ($versionExitCode -ne 0) {
    throw "Could not query the bootstrap Python version."
}
$version = [version]($versionText | Select-Object -Last 1)
if ($version -lt [version]"3.11.0") {
    throw "Python 3.11 or newer is required; found $version."
}

if (-not (Test-Path -LiteralPath $venvPython)) {
    New-Item -ItemType Directory -Force -Path $buildRoot | Out-Null
    Write-Host "Creating isolated sidecar build environment with Python $version..."
    Invoke-BootstrapPython -Arguments @("-m", "venv", $venvRoot)
}

$dependencyProbe = "import importlib.metadata as m; d={x.metadata['Name'].lower():x.version for x in m.distributions()}; print(d.get('pyinstaller','')+'|'+d.get('pyserial','')+'|'+d.get('typing_extensions',''))"
$oldPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$dependencyStatus = & $venvPython -c $dependencyProbe
$dependencyExitCode = $LASTEXITCODE
$ErrorActionPreference = $oldPreference
$dependenciesReady = $dependencyExitCode -eq 0 -and ($dependencyStatus | Select-Object -Last 1) -eq "6.16.0|3.5|4.15.0"
if (-not $dependenciesReady) {
    if ($SkipDependencyInstall) {
        throw "Pinned PyInstaller/pyserial dependencies are missing. Run again without -SkipDependencyInstall."
    }
    Write-Host "Installing pinned sidecar build dependencies..."
    $oldPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $venvPython -m pip install --disable-pip-version-check --requirement $requirements
    $dependencyInstallExitCode = $LASTEXITCODE
    $ErrorActionPreference = $oldPreference
    if ($dependencyInstallExitCode -ne 0) {
        throw "Installing sidecar build dependencies failed with exit code $dependencyInstallExitCode."
    }
}

New-Item -ItemType Directory -Force -Path $binaryDirectory, $workPath, $distPath, $specPath | Out-Null

# Keep the executable as a console-subsystem process: Rust starts it with
# CREATE_NO_WINDOW, while stdin/stdout must remain available for JSONL IPC.
$addData = "$factoryProfile;config"
Write-Host "Building KIIIe sidecar for $TargetTriple..."
$pythonBasePrefix = & $venvPython -c "import sys; print(sys.base_prefix)"
if ($LASTEXITCODE -ne 0) {
    throw "Could not resolve the sidecar build Python base prefix."
}
$pythonRuntimeBin = Join-Path ($pythonBasePrefix | Select-Object -Last 1) "Library\bin"
$originalPath = $env:PATH
if (Test-Path -LiteralPath $pythonRuntimeBin) {
    # Conda-style Python distributions keep extension-module dependencies
    # (OpenSSL, expat, libffi, lzma) here rather than next to python.exe.
    # PyInstaller resolves them through PATH during binary analysis.
    $env:PATH = "$pythonRuntimeBin;$env:PATH"
}
$oldPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& $venvPython -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --noupx `
    --name "kiiie-core" `
    --paths $sourceRoot `
    --add-data $addData `
    --hidden-import "serial" `
    --hidden-import "serial.tools.list_ports" `
    --exclude-module "setuptools" `
    --exclude-module "pkg_resources" `
    --distpath $distPath `
    --workpath $workPath `
    --specpath $specPath `
    $entryPoint
$pyInstallerExitCode = $LASTEXITCODE
$ErrorActionPreference = $oldPreference
$env:PATH = $originalPath
if ($pyInstallerExitCode -ne 0) {
    throw "PyInstaller failed with exit code $pyInstallerExitCode."
}

$builtBinary = Join-Path $distPath "kiiie-core.exe"
if (-not (Test-Path -LiteralPath $builtBinary)) {
    throw "PyInstaller completed without producing $builtBinary"
}

Copy-Item -LiteralPath $builtBinary -Destination $targetBinary -Force
$artifact = Get-Item -LiteralPath $targetBinary
$hash = Get-FileHash -Algorithm SHA256 -LiteralPath $targetBinary
Write-Host ("Sidecar ready: {0} ({1:N0} bytes, SHA256 {2})" -f $artifact.FullName, $artifact.Length, $hash.Hash)
