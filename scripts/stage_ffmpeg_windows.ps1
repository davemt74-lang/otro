param(
  [string]$Version = "9.0.2",
  [string]$Url = "https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-9.0.2-essentials_build.zip",
  [string]$Destination = "dist/tools/ffmpeg"
)

$ErrorActionPreference = "Stop"
$tmp = Join-Path $env:RUNNER_TEMP "vp3-ffmpeg"
$zip = Join-Path $tmp "ffmpeg.zip"
$checksumFile = Join-Path $tmp "ffmpeg.zip.sha256"
$extract = Join-Path $tmp "extract"

Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force $Destination -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $tmp,$extract,$Destination | Out-Null

Invoke-WebRequest -Uri $Url -OutFile $zip -UseBasicParsing
Invoke-WebRequest -Uri ($Url + ".sha256") -OutFile $checksumFile -UseBasicParsing

$checksumText = (Get-Content $checksumFile -Raw).Trim()
$checksumMatch = [regex]::Match($checksumText, "[0-9A-Fa-f]{64}")
if (-not $checksumMatch.Success) {
  throw "FFmpeg package checksum response was invalid"
}
$expectedPackageHash = $checksumMatch.Value.ToLowerInvariant()
$actualPackageHash = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualPackageHash -ne $expectedPackageHash) {
  throw "FFmpeg package checksum mismatch"
}

Expand-Archive -Path $zip -DestinationPath $extract -Force

$ffmpeg = Get-ChildItem -Path $extract -Recurse -File -Filter "ffmpeg.exe" | Select-Object -First 1
$ffprobe = Get-ChildItem -Path $extract -Recurse -File -Filter "ffprobe.exe" | Select-Object -First 1
if (-not $ffmpeg -or -not $ffprobe) {
  throw "FFmpeg package did not contain ffmpeg.exe and ffprobe.exe"
}

Copy-Item $ffmpeg.FullName (Join-Path $Destination "ffmpeg.exe") -Force
Copy-Item $ffprobe.FullName (Join-Path $Destination "ffprobe.exe") -Force

$ffmpegOut = & (Join-Path $Destination "ffmpeg.exe") -version
if ($LASTEXITCODE -ne 0) { throw "Managed ffmpeg.exe failed version check" }
$ffprobeOut = & (Join-Path $Destination "ffprobe.exe") -version
if ($LASTEXITCODE -ne 0) { throw "Managed ffprobe.exe failed version check" }

$ffmpegHash = (Get-FileHash (Join-Path $Destination "ffmpeg.exe") -Algorithm SHA256).Hash.ToLowerInvariant()
$ffprobeHash = (Get-FileHash (Join-Path $Destination "ffprobe.exe") -Algorithm SHA256).Hash.ToLowerInvariant()

$manifest = [ordered]@{
  contract = "vp3.homeserver.media-tools.v1"
  version = $Version
  source = $Url
  source_commit = "946fcce07b"
  package_sha256 = $actualPackageHash
  ffmpeg_sha256 = $ffmpegHash
  ffprobe_sha256 = $ffprobeHash
  ffmpeg_version = (($ffmpegOut | Select-Object -First 1) -join "")
  ffprobe_version = (($ffprobeOut | Select-Object -First 1) -join "")
}
$manifest | ConvertTo-Json -Depth 4 | Set-Content -Encoding utf8 (Join-Path $Destination "manifest.json")

$notice = @"
VP3 HomeServer Managed Media Runtime
FFmpeg version: $Version
Build source: $Url
Build provider: https://www.gyan.dev/ffmpeg/builds/
License: GPLv3 (as published by the build provider)
Corresponding FFmpeg source commit: https://github.com/FFmpeg/FFmpeg/commit/946fcce07b
FFmpeg project: https://ffmpeg.org/
The FFmpeg binaries are separate third-party executables managed by HomeServer.
"@
Set-Content -Encoding utf8 (Join-Path $Destination "THIRD_PARTY_NOTICE.txt") $notice

$packageReadme = Get-ChildItem -Path $extract -Recurse -File -Filter "README.txt" | Select-Object -First 1
if ($packageReadme) {
  Copy-Item $packageReadme.FullName (Join-Path $Destination "FFMPEG_BUILD_README.txt") -Force
}

Write-Host "Staged managed FFmpeg $Version"
Write-Host "package SHA256 $actualPackageHash"
Write-Host "ffmpeg SHA256 $ffmpegHash"
Write-Host "ffprobe SHA256 $ffprobeHash"
