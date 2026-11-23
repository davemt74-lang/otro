function Invoke-RuntimeDownload {
  [CmdletBinding()]
  param(
    [Parameter(Mandatory = $true)][string]$Uri,
    [Parameter(Mandatory = $true)][string]$OutFile,
    [ValidateRange(1, 6)][int]$MaxAttempts = 4,
    [ValidateRange(1, 30)][int]$BaseDelaySeconds = 2,
    [ValidateRange(1, 300)][int]$TimeoutSeconds = 120
  )

  for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
    # Never reuse a prior partial response as the package or checksum file.
    if (Test-Path -LiteralPath $OutFile) { Remove-Item -LiteralPath $OutFile -Force -ErrorAction Stop }
    try {
      Invoke-WebRequest -Uri $Uri -OutFile $OutFile -UseBasicParsing -TimeoutSec $TimeoutSeconds -ErrorAction Stop
      return
    } catch {
      if (Test-Path -LiteralPath $OutFile) { Remove-Item -LiteralPath $OutFile -Force -ErrorAction Stop }
      $httpStatus = $null
      $networkFailure = $false
      $failure = $_.Exception
      for ($depth = 0; $failure -and $depth -lt 10; $depth++) {
        $statusProperty = $failure.PSObject.Properties['StatusCode']
        if ($statusProperty -and $null -ne $statusProperty.Value) { $httpStatus = [int]$statusProperty.Value }
        $responseProperty = $failure.PSObject.Properties['Response']
        if ($null -eq $httpStatus -and $responseProperty -and $responseProperty.Value) {
          $responseStatus = $responseProperty.Value.PSObject.Properties['StatusCode']
          if ($responseStatus -and $null -ne $responseStatus.Value) { $httpStatus = [int]$responseStatus.Value }
        }
        if ($null -ne $httpStatus) { break }
        if ($failure -is [System.Net.Http.HttpRequestException] -or
            $failure -is [System.Net.WebException] -or
            $failure -is [System.TimeoutException] -or
            $failure -is [System.Threading.Tasks.TaskCanceledException]) { $networkFailure = $true }
        $failure = $failure.InnerException
      }
      $transient = if ($null -ne $httpStatus) { $httpStatus -eq 429 -or ($httpStatus -ge 500 -and $httpStatus -le 599) } else { $networkFailure }
      if (-not $transient -or $attempt -ge $MaxAttempts) { throw }
      $delay = [int][Math]::Min(30, $BaseDelaySeconds * [Math]::Pow(2, $attempt - 1))
      $category = if ($null -ne $httpStatus) { "HTTP $httpStatus" } else { 'network failure' }
      Write-Warning "Runtime download attempt $attempt/$MaxAttempts failed ($category); retrying in $delay seconds."
      Start-Sleep -Seconds $delay
    }
  }
}
