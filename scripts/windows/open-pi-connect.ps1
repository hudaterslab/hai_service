param(
  [switch]$OpenDocs
)

$ErrorActionPreference = "Stop"

Start-Process "https://connect.raspberrypi.com"

if ($OpenDocs) {
  Start-Process "https://www.raspberrypi.com/documentation/services/connect.html"
}

Write-Output "Opened: https://connect.raspberrypi.com"
if ($OpenDocs) {
  Write-Output "Opened docs: https://www.raspberrypi.com/documentation/services/connect.html"
}
Write-Output "Hybrid guide: C:\Users\dongs\vms-8ch-webrtc\docs\RPI_CONNECT_HYBRID_WORKFLOW.md"
