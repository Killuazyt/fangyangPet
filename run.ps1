param(
  [switch]$Mock,
  [switch]$Once,
  [switch]$TestSsh,
  [string]$Config = "$PSScriptRoot\config.json"
)

$ErrorActionPreference = "Stop"
$argsList = @("-m", "gpu_rowlet", "--config", $Config)
if ($Mock) { $argsList += "--mock" }
if ($Once) { $argsList += "--once" }
if ($TestSsh) { $argsList += "--test-ssh" }

Push-Location $PSScriptRoot
try {
  python @argsList
} finally {
  Pop-Location
}
