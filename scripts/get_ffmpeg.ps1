# Descarga un ffmpeg estático con libass (necesario para los subtítulos) a .\bin\
# (Windows x64 o ARM64). En macOS/Linux usa scripts/get_ffmpeg.sh.
#
#   powershell -ExecutionPolicy Bypass -File .\scripts\get_ffmpeg.ps1
$ErrorActionPreference = "Stop"
Set-Location -Path (Join-Path $PSScriptRoot "..")
New-Item -ItemType Directory -Force -Path "bin" | Out-Null

$base = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest"
$name = if ($env:PROCESSOR_ARCHITECTURE -eq "ARM64") { "ffmpeg-master-latest-winarm64-gpl.zip" } else { "ffmpeg-master-latest-win64-gpl.zip" }
$tmp = Join-Path ([IO.Path]::GetTempPath()) ("kc-ffmpeg-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $tmp | Out-Null
try {
  Write-Host "Descargando ffmpeg ($name)…"
  $zip = Join-Path $tmp "ffmpeg.zip"
  $ProgressPreference = "SilentlyContinue"  # la barra de progreso hace lentísima la descarga
  Invoke-WebRequest -Uri "$base/$name" -OutFile $zip -UseBasicParsing
  Expand-Archive -Path $zip -DestinationPath $tmp -Force
  $exe = Get-ChildItem -Path $tmp -Recurse -Filter "ffmpeg.exe" | Select-Object -First 1
  if (-not $exe) { throw "El zip no contiene ffmpeg.exe" }
  Copy-Item $exe.FullName -Destination "bin\ffmpeg.exe" -Force
}
finally {
  Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}

& ".\bin\ffmpeg.exe" -hide_banner -version | Select-Object -First 1
$filters = (& ".\bin\ffmpeg.exe" -hide_banner -filters 2>$null) -join "`n"
if ($filters -match "\bass\b") { Write-Host "✓ libass disponible (subtítulos OK)" }
else { Write-Host "⚠ este build no trae libass" }
