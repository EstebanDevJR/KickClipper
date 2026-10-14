# Lanza KickClipper en Windows (PowerShell). En macOS/Linux usa run.sh.
#
#   .\run.ps1           # arranca el servidor y abre la UI en el navegador
#   .\run.ps1 stop      # detiene el servidor
#   .\run.ps1 restart   # lo detiene y lo vuelve a arrancar
#
# Si PowerShell no deja ejecutar scripts:
#   powershell -ExecutionPolicy Bypass -File .\run.ps1
param([string]$Command = "")
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
$env:UV_NO_EDITABLE = "1"

$Port = if ($env:KC_PORT) { [int]$env:KC_PORT } else { 8420 }
$Url = "http://127.0.0.1:$Port"

function Get-ServerPids {
  Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique
}

function Stop-Server {
  $pids = Get-ServerPids
  if (-not $pids) { Write-Host "El servidor no estaba corriendo (puerto $Port libre)."; return }
  foreach ($p in $pids) { Stop-Process -Id $p -Force -ErrorAction SilentlyContinue }
  Write-Host "✓ Servidor detenido."
}

function Test-Server {
  try { Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 1 | Out-Null; return $true }
  catch { return $false }
}

if ($Command -in @("stop", "restart")) {
  Stop-Server
  if ($Command -eq "stop") { exit 0 }
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  Write-Host "Falta uv. Instálalo con:"
  Write-Host '  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"'
  exit 1
}

if (Test-Server) {
  Write-Host "KickClipper ya está corriendo en $Url"
  if (-not $env:KC_NO_OPEN) { Start-Process $Url }
  exit 0
}

# Abre la UI en cuanto el servidor responda (KC_NO_OPEN=1 lo desactiva).
if (-not $env:KC_NO_OPEN) {
  Start-Job -ArgumentList $Url -ScriptBlock {
    param($u)
    for ($i = 0; $i -lt 120; $i++) {
      try { Invoke-WebRequest -Uri $u -UseBasicParsing -TimeoutSec 1 | Out-Null; Start-Process $u; return }
      catch { Start-Sleep -Milliseconds 500 }
    }
  } | Out-Null
}

uv run kickclipper
