param(
  [string]$HostName,
  [int]$Port = 22,
  [string]$Username = "ubuntu",
  [string]$IdentityFile = "$env:USERPROFILE\.ssh\id_ed25519"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $IdentityFile)) {
  Write-Host "Generating SSH key: $IdentityFile"
  ssh-keygen -t ed25519 -f $IdentityFile -C "gpu-rowlet-monitor" 
}

Write-Host ""
Write-Host "Public key to add to the Ubuntu user's ~/.ssh/authorized_keys:"
Get-Content -LiteralPath "$IdentityFile.pub"

if ($HostName) {
  Write-Host ""
  Write-Host "Testing SSH host key and nvidia-smi. Confirm the server fingerprint if prompted."
  ssh -i $IdentityFile -p $Port "$Username@$HostName" "command -v nvidia-smi >/dev/null && nvidia-smi -L"
}
