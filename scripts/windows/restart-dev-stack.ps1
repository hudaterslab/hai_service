$ErrorActionPreference = "Stop"

$python = "C:\Users\dongs\AppData\Local\Programs\Python\Python314\python.exe"
$root = "C:\Users\dongs\vms-8ch-webrtc"
$api = Join-Path $root "services\api\dev_server.py"
$worker = Join-Path $root "services\recorder\dev_worker.py"

# Stop existing python processes we own.
Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 1

Start-Process -FilePath $python -ArgumentList @($api) -WindowStyle Hidden
Start-Sleep -Seconds 1
Start-Process -FilePath $python -ArgumentList @("-u", $worker) -WindowStyle Hidden

Start-Sleep -Seconds 3

Write-Output "health:"
(Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8080/healthz).Content
Write-Output "person-event:"
(Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8080/settings/person-event).Content
Write-Output "monitor:"
(Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8080/monitor/cameras).Content
