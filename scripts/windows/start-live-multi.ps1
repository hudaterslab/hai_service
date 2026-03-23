param(
  [string]$ApiBaseUrl = "http://127.0.0.1:8080",
  [string]$RtspFallbackPath = "/Streaming/Channels/101",
  [string]$MediaMtxExe = "C:\Users\dongs\vms-8ch-webrtc\bin\mediamtx.exe",
  [string]$FfmpegExe = "C:\Users\dongs\vms-8ch-webrtc\bin\ffmpeg.exe",
  [switch]$NoStartMediaMtx
)

$ErrorActionPreference = "Stop"
$root = "C:\Users\dongs\vms-8ch-webrtc"
$runtimeDir = Join-Path $root "data\runtime"
$configPath = Join-Path $root "deploy\mediamtx.yml"
New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null

if (-not (Test-Path $FfmpegExe)) {
  throw "ffmpeg.exe not found: $FfmpegExe"
}
if (-not $NoStartMediaMtx) {
  if (-not (Test-Path $MediaMtxExe)) {
    throw "mediamtx.exe not found: $MediaMtxExe"
  }
  if (-not (Test-Path $configPath)) {
    throw "mediamtx config not found: $configPath"
  }
}

function Normalize-RtspUrl([string]$url, [string]$fallbackPath) {
  if (-not $url) { return $url }
  if ($url -match '^rtsp://[^/]+$') {
    return "$url$fallbackPath"
  }
  if ($url -match '^rtsp://[^/]+/$') {
    return ($url.TrimEnd('/')) + $fallbackPath
  }
  return $url
}

function Sanitize-Name([string]$s) {
  if (-not $s) { return "unknown" }
  return ($s -replace '[^A-Za-z0-9._-]', '-')
}

function Is-RunningPidFile([string]$pidFile) {
  if (-not (Test-Path $pidFile)) { return $false }
  try {
    $pid = [int](Get-Content $pidFile -ErrorAction Stop | Select-Object -First 1)
    $p = Get-Process -Id $pid -ErrorAction Stop
    return ($p -ne $null)
  } catch {
    return $false
  }
}

if (-not $NoStartMediaMtx) {
  $mtxRunning = Get-Process -Name mediamtx -ErrorAction SilentlyContinue | Select-Object -First 1
  if (-not $mtxRunning) {
    $mtxOut = Join-Path $runtimeDir "mediamtx.out.log"
    $mtxErr = Join-Path $runtimeDir "mediamtx.err.log"
    $mtx = Start-Process -FilePath $MediaMtxExe -ArgumentList $configPath -PassThru -WindowStyle Hidden -RedirectStandardOutput $mtxOut -RedirectStandardError $mtxErr
    Set-Content -Path (Join-Path $runtimeDir "mediamtx.pid") -Value $mtx.Id -Encoding ascii
    Start-Sleep -Seconds 1
    Write-Output "started mediamtx pid=$($mtx.Id)"
  } else {
    Write-Output "mediamtx already running pid=$($mtxRunning.Id)"
  }
}

$camerasResp = Invoke-WebRequest -UseBasicParsing "$ApiBaseUrl/cameras"
$cameras = $camerasResp.Content | ConvertFrom-Json
if (-not $cameras) {
  Write-Output "no cameras returned from $ApiBaseUrl/cameras"
  exit 0
}

foreach ($cam in $cameras) {
  if (-not $cam.enabled) { continue }
  if (-not $cam.webrtcPath) { continue }

  $streamPath = [string]$cam.webrtcPath
  $safeName = Sanitize-Name $streamPath
  $pidFile = Join-Path $runtimeDir "publisher-$safeName.pid"
  if (Is-RunningPidFile $pidFile) {
    Write-Output "skip running publisher stream=$streamPath"
    continue
  }

  $rtspUrl = Normalize-RtspUrl ([string]$cam.rtspUrl) $RtspFallbackPath
  $pubOut = Join-Path $runtimeDir "publisher-$safeName.out.log"
  $pubErr = Join-Path $runtimeDir "publisher-$safeName.err.log"
  $publishUrl = "rtmp://127.0.0.1:1935/$streamPath"
  $ffArgs = @(
    "-hide_banner",
    "-loglevel", "info",
    "-rtsp_transport", "tcp",
    "-i", $rtspUrl,
    "-an",
    "-c:v", "libx264",
    "-preset", "ultrafast",
    "-tune", "zerolatency",
    "-pix_fmt", "yuv420p",
    "-f", "flv",
    $publishUrl
  )

  $pub = Start-Process -FilePath $FfmpegExe -ArgumentList $ffArgs -PassThru -WindowStyle Hidden -RedirectStandardOutput $pubOut -RedirectStandardError $pubErr
  Set-Content -Path $pidFile -Value $pub.Id -Encoding ascii
  Write-Output "started publisher stream=$streamPath pid=$($pub.Id)"
}

