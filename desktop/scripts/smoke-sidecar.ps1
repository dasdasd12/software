[CmdletBinding()]
param(
    [string]$TargetTriple = "x86_64-pc-windows-msvc"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$desktopRoot = Split-Path -Parent $PSScriptRoot
$softwareRoot = Split-Path -Parent $desktopRoot
$binary = Join-Path $desktopRoot "src-tauri\binaries\kiiie-core-$TargetTriple.exe"
$profilePath = Join-Path $softwareRoot "config\factory_default_profile.json"

if (-not (Test-Path -LiteralPath $binary)) {
    throw "Sidecar binary was not found: $binary"
}

$profile = Get-Content -Raw -Encoding UTF8 -LiteralPath $profilePath | ConvertFrom-Json
$requests = @(
    @{ id = "smoke-hello"; method = "bridge.hello"; params = @{} },
    @{ id = "smoke-compile"; method = "profile.compile"; params = @{ profile = $profile } }
)
$jsonl = ($requests | ForEach-Object { $_ | ConvertTo-Json -Depth 100 -Compress }) -join "`n"
$responses = $jsonl | & $binary --stdio
if ($LASTEXITCODE -ne 0) {
    throw "The sidecar exited with code $LASTEXITCODE."
}

$decoded = @($responses | ForEach-Object { $_ | ConvertFrom-Json })
$hello = $decoded | Where-Object { $_.id -eq "smoke-hello" } | Select-Object -First 1
$compile = $decoded | Where-Object { $_.id -eq "smoke-compile" } | Select-Object -First 1
if (-not $hello -or -not $hello.ok) {
    throw "bridge.hello smoke request failed: $($hello | ConvertTo-Json -Depth 10 -Compress)"
}
if (-not $compile -or -not $compile.ok) {
    throw "profile.compile smoke request failed: $($compile | ConvertTo-Json -Depth 10 -Compress)"
}

Write-Host ("bridge.hello ok: protocol {0}, {1} methods" -f $hello.result.protocolVersion, $hello.result.methods.Count)
Write-Host ("profile.compile ok: {0:N0} bytes, {1} warnings" -f $compile.result.packageSize, $compile.result.warnings.Count)
