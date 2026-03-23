param(
  [string]$CameraRtsp = "rtsp://192.168.10.78:554/Streaming/Channels/101",
  [string]$StreamPath = "cam-192-168-10-78",
  [string]$MediaMtxExe = "C:\Users\dongs\vms-8ch-webrtc\bin\mediamtx.exe",
  [string]$FfmpegExe = "C:\Users\dongs\vms-8ch-webrtc\bin\ffmpeg.exe",
  [switch]$NoStartMediaMtx
)

$ErrorActionPreference = "Stop"
$root = "C:\Users\dongs\vms-8ch-webrtc"
$runtimeDir = Join-Path $root "data\runtime"
$configPath = Join-Path $root "deploy\mediamtx.yml"
New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null

if (-not (Test-Path $MediaMtxExe)) {
  throw "mediamtx.exe not found: $MediaMtxExe"
}
if (-not (Test-Path $FfmpegExe)) {
  throw "ffmpeg.exe not found: $FfmpegExe"
}
if (-not (Test-Path $configPath)) {
  throw "mediamtx config not found: $configPath"
}

$mtxOut = Join-Path $runtimeDir "mediamtx.out.log"
$mtxErr = Join-Path $runtimeDir "mediamtx.err.log"
$pubOut = Join-Path $runtimeDir "publisher.out.log"
$pubErr = Join-Path $runtimeDir "publisher.err.log"

Get-Process -Name mediamtx,ffmpeg -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 600

$mtx = $null
if (-not $NoStartMediaMtx) {
  $mtx = Start-Process -FilePath $MediaMtxExe -ArgumentList $configPath -PassThru -WindowStyle Hidden -RedirectStandardOutput $mtxOut -RedirectStandardError $mtxErr
  Start-Sleep -Seconds 1
}

$publishUrl = "rtmp://127.0.0.1:1935/$StreamPath"
$ffArgs = @(
  "-hide_banner",
  "-loglevel", "info",
  "-rtsp_transport", "tcp",
  "-i", $CameraRtsp,
  "-an",
  "-c:v", "libx264",
  "-preset", "ultrafast",
  "-tune", "zerolatency",
  "-pix_fmt", "yuv420p",
  "-f", "flv",
  $publishUrl
)
$pub = Start-Process -FilePath $FfmpegExe -ArgumentList $ffArgs -PassThru -WindowStyle Hidden -RedirectStandardOutput $pubOut -RedirectStandardError $pubErr

if ($mtx -ne $null) {
  Set-Content -Path (Join-Path $runtimeDir "mediamtx.pid") -Value $mtx.Id -Encoding ascii
}
Set-Content -Path (Join-Path $runtimeDir "publisher.pid") -Value $pub.Id -Encoding ascii

if ($mtx -ne $null) {
  Write-Output "started mediamtx pid=$($mtx.Id)"
} else {
  Write-Output "mediamtx start skipped"
}
Write-Output "started publisher pid=$($pub.Id)"
Write-Output "webrtc url: http://127.0.0.1:8889/$StreamPath"
