$ErrorActionPreference = "Stop"

$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $root

docker compose -f deploy/docker-compose.dev.yml up -d --build
Start-Sleep -Seconds 3

Write-Output "health:"
try {
  (Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8080/healthz).Content
} catch {
  Write-Output $_.Exception.Message
}
