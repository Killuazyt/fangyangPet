param(
    [switch]$Hard
)

$ErrorActionPreference = "Stop"

$exePath = Join-Path $PSScriptRoot "fangyangPet.exe"
if (Test-Path -LiteralPath $exePath) {
    (Get-Item -LiteralPath $exePath).LastWriteTime = Get-Date
}

$ie4uinit = Join-Path $env:WINDIR "System32\ie4uinit.exe"
if (Test-Path -LiteralPath $ie4uinit) {
    & $ie4uinit -show | Out-Null
}

if (-not $Hard) {
    Write-Host "Icon refresh requested. If Explorer still shows the old icon, run:"
    Write-Host "  powershell -ExecutionPolicy Bypass -File .\refresh_icon_cache.ps1 -Hard"
    exit 0
}

$explorerCache = Join-Path $env:LOCALAPPDATA "Microsoft\Windows\Explorer"
$iconCache = Join-Path $env:LOCALAPPDATA "IconCache.db"

Stop-Process -Name explorer -Force -ErrorAction SilentlyContinue
Start-Sleep -Milliseconds 500

if (Test-Path -LiteralPath $iconCache) {
    Remove-Item -LiteralPath $iconCache -Force -ErrorAction SilentlyContinue
}

if (Test-Path -LiteralPath $explorerCache) {
    Get-ChildItem -LiteralPath $explorerCache -Filter "iconcache*.db" -File -ErrorAction SilentlyContinue |
        Remove-Item -Force -ErrorAction SilentlyContinue
    Get-ChildItem -LiteralPath $explorerCache -Filter "thumbcache*.db" -File -ErrorAction SilentlyContinue |
        Remove-Item -Force -ErrorAction SilentlyContinue
}

Start-Process explorer.exe
Write-Host "Explorer icon cache cleared."
