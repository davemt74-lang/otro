$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '../scripts/runtime_download.ps1')

$fixture = Join-Path ([System.IO.Path]::GetTempPath()) ('vp3-download-retry-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $fixture | Out-Null
$script:Plan = [System.Collections.Generic.Queue[object]]::new()
$script:Requests = [System.Collections.Generic.List[string]]::new()
$script:Sleeps = [System.Collections.Generic.List[int]]::new()

function Assert-True([bool]$Value, [string]$Message) { if (-not $Value) { throw $Message } }
function Start-Sleep { param([int]$Seconds) $script:Sleeps.Add($Seconds) }
function Invoke-WebRequest {
  [CmdletBinding()]
  param([string]$Uri, [string]$OutFile, [switch]$UseBasicParsing, [int]$TimeoutSec)
  Assert-True (-not (Test-Path -LiteralPath $OutFile)) 'A partial download survived into a later attempt'
  $script:Requests.Add($Uri)
  Assert-True ($script:Plan.Count -gt 0) 'Unexpected network attempt'
  $next = $script:Plan.Dequeue()
  if ($next -is [string] -and $next -eq 'success') { Set-Content -LiteralPath $OutFile -Value 'complete fixture bytes' -NoNewline; return }
  Set-Content -LiteralPath $OutFile -Value 'partial fixture bytes' -NoNewline
  if ($next -is [string] -and $next -eq 'network') { throw [System.Net.Http.HttpRequestException]::new('Synthetic network interruption') }
  if ($next -is [string] -and $next -eq 'timeout') { throw [System.TimeoutException]::new('Synthetic timeout') }
  throw [System.Net.Http.HttpRequestException]::new('Synthetic HTTP response', $null, [System.Net.HttpStatusCode][int]$next)
}
function Set-Scenario([object[]]$Steps) {
  $script:Plan.Clear(); $script:Requests.Clear(); $script:Sleeps.Clear()
  foreach ($step in $Steps) { $script:Plan.Enqueue($step) }
}

try {
  foreach ($name in @('ffmpeg.zip', 'ffmpeg.zip.sha256')) {
    $target = Join-Path $fixture $name
    Set-Content -LiteralPath $target -Value 'stale partial response'
    Set-Scenario @(503, 502, 'success')
    Invoke-RuntimeDownload -Uri "https://fixture.invalid/$name" -OutFile $target
    Assert-True ($script:Requests.Count -eq 3 -and $script:Plan.Count -eq 0) 'Transient HTTP responses were not retried exactly twice'
    Assert-True (($script:Sleeps -join ',') -eq '2,4') 'Download retry backoff was not bounded and ordered'
    Assert-True ((Get-Content -LiteralPath $target -Raw) -eq 'complete fixture bytes') 'The final download did not replace partial bytes'
    Write-Host "PASS transient HTTP retries and partial-file replacement: $name"
  }
  $target = Join-Path $fixture 'failure.zip'
  Set-Scenario @(404, 'success')
  $failed = $false
  try { Invoke-RuntimeDownload -Uri 'https://fixture.invalid/permanent' -OutFile $target } catch { $failed = $true }
  Assert-True ($failed -and $script:Requests.Count -eq 1 -and $script:Sleeps.Count -eq 0) 'A permanent HTTP failure was retried or accepted'
  Assert-True (-not (Test-Path -LiteralPath $target)) 'Permanent failure left a partial download'
  Write-Host 'PASS permanent HTTP 404 fails immediately and clears partial bytes'

  Set-Scenario @(503, 503, 503, 503, 'success')
  $failed = $false
  try { Invoke-RuntimeDownload -Uri 'https://fixture.invalid/unavailable' -OutFile $target } catch { $failed = $true }
  Assert-True ($failed -and $script:Requests.Count -eq 4 -and $script:Plan.Count -eq 1) 'Retry exhaustion did not fail after the bounded attempt count'
  Assert-True (($script:Sleeps -join ',') -eq '2,4,8') 'Retry exhaustion used unexpected delays'
  Assert-True (-not (Test-Path -LiteralPath $target)) 'Retry exhaustion left a partial download'
  Write-Host 'PASS retry exhaustion fails closed after four attempts'

  Set-Scenario @('network', 429, 'timeout', 'success')
  Invoke-RuntimeDownload -Uri 'https://fixture.invalid/network' -OutFile $target
  Assert-True ($script:Requests.Count -eq 4 -and ($script:Sleeps -join ',') -eq '2,4,8') 'Network, rate-limit and timeout failures did not recover within the attempt budget'
  Write-Host 'PASS network failures, HTTP 429 and timeouts use the same bounded retry policy'
} finally {
  Remove-Item -LiteralPath $fixture -Recurse -Force
}
