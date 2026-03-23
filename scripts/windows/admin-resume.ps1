param(
  [string]$PythonExe = "C:\Users\dongs\AppData\Local\Programs\Python\Python314\python.exe",
  [int]$LivePollSec = 3,
  [switch]$OpenDashboard
)

$ErrorActionPreference = "Stop"
$root = "C:\Users\dongs\vms-8ch-webrtc"
$apiScript = Join-Path $root "services\api\dev_server.py"
$workerScript = Join-Path $root "services\recorder\dev_worker.py"
$liveManagerScript = Join-Path $root "scripts\windows\live-publisher-manager.ps1"

if (-not (Test-Path $PythonExe)) { throw "python not found: $PythonExe" }
if (-not (Test-Path $apiScript)) { throw "api script not found: $apiScript" }
if (-not (Test-Path $workerScript)) { throw "worker script not found: $workerScript" }
if (-not (Test-Path $liveManagerScript)) { throw "live manager script not found: $liveManagerScript" }

function Test-Admin {
  $id = [Security.Principal.WindowsIdentity]::GetCurrent()
  $p = New-Object Security.Principal.WindowsPrincipal($id)
  return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-Admin)) {
  throw "Run this script in Administrator PowerShell."
}

Get-Process -Name python,mediamtx,ffmpeg -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 500

Start-Process -FilePath $PythonExe -ArgumentList $apiScript
Start-Process -FilePath $PythonExe -ArgumentList "-u $workerScript"
Start-Sleep -Seconds 1

Start-Process -FilePath "powershell" -ArgumentList @(
  "-ExecutionPolicy", "Bypass",
  "-File", $liveManagerScript,
  "-ApiBaseUrl", "http://127.0.0.1:8080",
  "-PollSec", "$LivePollSec"
)
Start-Sleep -Seconds 2

$apiOk = (Test-NetConnection 127.0.0.1 -Port 8080 -WarningAction SilentlyContinue).TcpTestSucceeded
$webrtcOk = (Test-NetConnection 127.0.0.1 -Port 8889 -WarningAction SilentlyContinue).TcpTestSucceeded

Write-Output "API(8080): $apiOk"
Write-Output "WebRTC(8889): $webrtcOk"
Write-Output "LivePublisherManager: started (poll=${LivePollSec}s)"
Write-Output "Dashboard: http://127.0.0.1:8080/"
Write-Output "WebRTC path rule: http://127.0.0.1:8889/<camera.webrtcPath>"

if ($OpenDashboard) {
  Start-Process "http://127.0.0.1:8080/"
}
