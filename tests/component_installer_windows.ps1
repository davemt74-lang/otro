$ErrorActionPreference = 'Stop'
$target = Join-Path $env:RUNNER_TEMP 'HomeServer Component Test'
$private = Join-Path $env:RUNNER_TEMP 'homeserver-component-private'
$env:HOMESERVER_DATA_DIR = $private
Remove-Item $target -Recurse -Force -ErrorAction SilentlyContinue
New-Item $private -ItemType Directory -Force | Out-Null
Set-Content (Join-Path $private 'sentinel.txt') 'keep'
function Install([string]$Setup) {
    $p = Start-Process -FilePath (Resolve-Path $Setup) -ArgumentList @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART',"/DIR=`"$target`"",'/TASKS=""') -Wait -PassThru
    return $p.ExitCode
}
if ((Install 'dist/HomeServerCoreSetup.exe') -eq 0 -or (Test-Path (Join-Path $target 'HomeServer.exe'))) { throw 'Core update accepted an empty installation' }
if ((Install 'dist/FullFixtureSetup.exe') -ne 0) { throw 'Full fixture install failed' }
$before = Get-FileHash (Join-Path $target 'HomeServer.exe')
$runtime = @{}
Get-ChildItem (Join-Path $target 'tools') -Recurse -File | ForEach-Object { $runtime[$_.FullName] = (Get-FileHash $_.FullName).Hash }
if ((Install 'dist/HomeServerCoreSetup.exe') -ne 0) { throw 'Core update failed' }
$after = (Get-FileHash (Join-Path $target 'HomeServer.exe')).Hash
if ($before.Hash -eq $after) { throw 'Core update did not replace application' }
foreach ($path in $runtime.Keys) { if ((Get-FileHash $path).Hash -ne $runtime[$path]) { throw 'Core update modified runtime' } }
$browser = Join-Path $target 'tools/browser/chrome.exe'
Set-Content $browser 'corrupt'
if ((Install 'dist/HomeServerCoreSetup.exe') -eq 0) { throw 'Core accepted corrupt browser' }
if ((Get-FileHash (Join-Path $target 'HomeServer.exe')).Hash -ne $after) { throw 'Blocked update changed application' }
Copy-Item 'dist/tools/browser/chrome.exe' $browser -Force
Remove-Item (Join-Path $target 'tools/ffmpeg/ffprobe.exe')
if ((Install 'dist/HomeServerCoreSetup.exe') -eq 0) { throw 'Core accepted missing media dependency' }
if ((Get-FileHash (Join-Path $target 'HomeServer.exe')).Hash -ne $after) { throw 'Blocked update changed application' }
if ((Get-Content (Join-Path $private 'sentinel.txt') -Raw).Trim() -ne 'keep') { throw 'Private data changed' }
Write-Host 'Core update, runtime reuse, missing/corrupt dependency blocking and private data passed'
