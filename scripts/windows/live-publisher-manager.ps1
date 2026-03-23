param(
  [string]$ApiBaseUrl = "http://127.0.0.1:8080",
  [int]$PollSec = 3,
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

function Get-PublisherPaths() {
  return Get-ChildItem -Path $runtimeDir -Filter "publisher-*.pid" -ErrorAction SilentlyContinue
}

function Get-PublisherFileParts([string]$pidFilePath) {
  $name = [System.IO.Path]::GetFileNameWithoutExtension($pidFilePath)
  # publisher-<stream-safe>
  $safe = $name.Substring("publisher-".Length)
  $meta = Join-Path $runtimeDir "$name.json"
  return @{ SafeName = $safe; MetaFile = $meta }
}

function Read-Pid([string]$pidFile) {
  try {
    return [int](Get-Content $pidFile -ErrorAction Stop | Select-Object -First 1)
  } catch {
    return $null
  }
}

function Is-Running([int]$procId) {
  if (-not $procId) { return $false }
  try {
    $p = Get-Process -Id $procId -ErrorAction Stop
    return ($p -ne $null)
  } catch {
    return $false
  }
}

function Stop-PublisherByPidFile([string]$pidFilePath) {
  $procId = Read-Pid $pidFilePath
  if ($procId -and (Is-Running $procId)) {
    try { Stop-Process -Id $procId -Force } catch {}
  }
  Remove-Item $pidFilePath -Force -ErrorAction SilentlyContinue
  $parts = Get-PublisherFileParts $pidFilePath
  Remove-Item $parts.MetaFile -Force -ErrorAction SilentlyContinue
}

function Ensure-MediaMtx() {
  if ($NoStartMediaMtx) { return }
  $running = Get-Process -Name mediamtx -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($running) { return }
  $mtxOut = Join-Path $runtimeDir "mediamtx.out.log"
  $mtxErr = Join-Path $runtimeDir "mediamtx.err.log"
  $mtx = Start-Process -FilePath $MediaMtxExe -ArgumentList $configPath -PassThru -WindowStyle Hidden -RedirectStandardOutput $mtxOut -RedirectStandardError $mtxErr
  Set-Content -Path (Join-Path $runtimeDir "mediamtx.pid") -Value $mtx.Id -Encoding ascii
  Write-Output "started mediamtx pid=$($mtx.Id)"
  Start-Sleep -Seconds 1
}

function Load-Cameras() {
  $resp = Invoke-WebRequest -UseBasicParsing "$ApiBaseUrl/cameras"
  $items = $resp.Content | ConvertFrom-Json
  if (-not $items) { return @() }
  return @($items)
}

function Start-Or-RestartPublisher([string]$streamPath, [string]$rtspUrl) {
  $safe = Sanitize-Name $streamPath
  $pidFile = Join-Path $runtimeDir "publisher-$safe.pid"
  $metaFile = Join-Path $runtimeDir "publisher-$safe.json"

  $needsRestart = $false
  if (Test-Path $metaFile) {
    try {
      $meta = Get-Content $metaFile -Raw | ConvertFrom-Json
      if ($meta.rtspUrl -ne $rtspUrl) {
        $needsRestart = $true
      }
    } catch {
      $needsRestart = $true
    }
  }

  $procId = Read-Pid $pidFile
  $running = $procId -and (Is-Running $procId)
  if ($running -and -not $needsRestart) {
    return
  }
  if ($running -and $needsRestart) {
    try { Stop-Process -Id $procId -Force } catch {}
    Start-Sleep -Milliseconds 300
  }

  $publishUrl = "rtmp://127.0.0.1:1935/$streamPath"
  $pubOut = Join-Path $runtimeDir "publisher-$safe.out.log"
  $pubErr = Join-Path $runtimeDir "publisher-$safe.err.log"
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
  Set-Content -Path $metaFile -Value (@{ streamPath = $streamPath; rtspUrl = $rtspUrl } | ConvertTo-Json -Compress) -Encoding ascii
  Write-Output "publisher up stream=$streamPath pid=$($pub.Id)"
}

Set-Content -Path (Join-Path $runtimeDir "publisher-manager.pid") -Value $PID -Encoding ascii
Write-Output "live publisher manager started. api=$ApiBaseUrl poll=${PollSec}s"

while ($true) {
  try {
    Ensure-MediaMtx

    $desired = @{}
    $cams = Load-Cameras
    foreach ($cam in $cams) {
      if (-not $cam.enabled) { continue }
      $streamPath = [string]$cam.webrtcPath
      if (-not $streamPath) { continue }
      $rtsp = Normalize-RtspUrl ([string]$cam.rtspUrl) $RtspFallbackPath
      if (-not $rtsp) { continue }
      $desired[$streamPath] = $rtsp
    }

    foreach ($streamPath in $desired.Keys) {
      Start-Or-RestartPublisher -streamPath $streamPath -rtspUrl $desired[$streamPath]
    }

    $pidFiles = Get-PublisherPaths
    foreach ($f in $pidFiles) {
      $parts = Get-PublisherFileParts $f.FullName
      $metaFile = $parts.MetaFile
      $streamPath = $null
      if (Test-Path $metaFile) {
        try {
          $meta = Get-Content $metaFile -Raw | ConvertFrom-Json
          $streamPath = [string]$meta.streamPath
        } catch {}
      }
      if (-not $streamPath -or -not $desired.ContainsKey($streamPath)) {
        Stop-PublisherByPidFile $f.FullName
        if ($streamPath) {
          Write-Output "publisher down stream=$streamPath"
        } else {
          Write-Output "publisher down unknown-meta file=$($f.Name)"
        }
      }
    }
  } catch {
    Write-Output "manager loop error: $($_.Exception.Message)"
  }

  Start-Sleep -Seconds ([Math]::Max($PollSec, 1))
}
