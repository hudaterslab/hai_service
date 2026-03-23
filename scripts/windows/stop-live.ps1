$ErrorActionPreference = "SilentlyContinue"
$runtimeDir = "C:\Users\dongs\vms-8ch-webrtc\data\runtime"

function Stop-ByPidFile([string]$name) {
  $pidFile = Join-Path $runtimeDir "$name.pid"
  if (-not (Test-Path $pidFile)) { return }
  $pid = Get-Content $pidFile | Select-Object -First 1
  if ($pid) {
    Stop-Process -Id ([int]$pid) -Force
  }
  Remove-Item $pidFile -Force
}

Stop-ByPidFile "publisher"
Stop-ByPidFile "publisher-manager"
Get-ChildItem -Path $runtimeDir -Filter "publisher-*.pid" -ErrorAction SilentlyContinue | ForEach-Object {
  $pid = Get-Content $_.FullName | Select-Object -First 1
  if ($pid) {
    Stop-Process -Id ([int]$pid) -Force
  }
  Remove-Item $_.FullName -Force
  $meta = [System.IO.Path]::ChangeExtension($_.FullName, ".json")
  if (Test-Path $meta) { Remove-Item $meta -Force }
}
Stop-ByPidFile "mediamtx"
Write-Output "live stack stopped"
