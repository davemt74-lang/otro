param([string]$Installer = 'dist/HomeServerSetup.exe')
$ErrorActionPreference = 'Stop'
$installerPath = (Resolve-Path $Installer).Path
$root = Join-Path $env:RUNNER_TEMP 'homeserver-space-acceptance'
New-Item -ItemType Directory -Force $root | Out-Null
$env:HOMESERVER_DATA_DIR = Join-Path $root 'private-data'
New-Item -ItemType Directory -Force $env:HOMESERVER_DATA_DIR | Out-Null
$sentinel = Join-Path $env:HOMESERVER_DATA_DIR 'preserve.txt'
Set-Content $sentinel 'private-state'
$report = Join-Path $root 'native-tests.txt'
$p = Start-Process $installerPath -ArgumentList @('/VERYSILENT', '/SUPPRESSMSGBOXES', "/SPACESELFTEST=`"$report`"") -PassThru -Wait
if (-not (Test-Path $report) -or (Get-Content $report -Raw) -notmatch '^PASS ') { throw 'Native space tests did not pass' }

foreach ($scenario in @('low','unknown')) {
  $target = Join-Path $root "blocked-$scenario"
  $p = Start-Process $installerPath -ArgumentList @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART',"/DIR=`"$target`"", "/SPACEFIXTURE=$scenario") -PassThru -Wait
  if ($p.ExitCode -eq 0 -or (Test-Path (Join-Path $target 'HomeServer.exe'))) { throw "Silent $scenario space did not fail before installation" }
}

Add-Type -AssemblyName System.Drawing
Add-Type @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
public class SpaceWindow {
  public delegate bool Callback(IntPtr h, IntPtr p);
  [DllImport("user32.dll")] static extern bool EnumWindows(Callback cb, IntPtr p);
  [DllImport("user32.dll")] static extern bool EnumChildWindows(IntPtr h, Callback cb, IntPtr p);
  [DllImport("user32.dll",CharSet=CharSet.Unicode)] static extern int GetClassName(IntPtr h,StringBuilder b,int n);
  [DllImport("user32.dll",CharSet=CharSet.Unicode)] static extern IntPtr SendMessage(IntPtr h,uint m,IntPtr w,StringBuilder l);
  [DllImport("user32.dll")] static extern IntPtr SendMessage(IntPtr h,uint m,IntPtr w,IntPtr l);
  [DllImport("user32.dll")] public static extern bool IsWindowEnabled(IntPtr h);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h,out uint p);
  [DllImport("user32.dll")] static extern bool GetWindowRect(IntPtr h,out Rect r);
  [StructLayout(LayoutKind.Sequential)] public struct Rect { public int Left,Top,Right,Bottom; }
  public static string Text(IntPtr h) {var b=new StringBuilder(32768);SendMessage(h,13,(IntPtr)b.Capacity,b);return b.ToString();}
  public static IntPtr Wizard() {IntPtr found=IntPtr.Zero;EnumWindows((h,p)=>{var b=new StringBuilder(100);GetClassName(h,b,100);if(b.ToString()=="TWizardForm" && Text(h).Contains("HomeServer")){found=h;return false;}return true;},IntPtr.Zero);return found;}
  public static IntPtr[] Children(IntPtr h) {var a=new List<IntPtr>();EnumChildWindows(h,(c,p)=>{a.Add(c);return true;},IntPtr.Zero);return a.ToArray();}
  public static IntPtr Button(IntPtr h,string name) {foreach(var c in Children(h))if(IsWindowVisible(c)&&Text(c).Replace("&","")==name)return c;return IntPtr.Zero;}
  public static void Click(IntPtr h) {SendMessage(h,245,IntPtr.Zero,IntPtr.Zero);}
  public static uint Pid(IntPtr h) {uint p;GetWindowThreadProcessId(h,out p);return p;}
  public static Rect Bounds(IntPtr h) {Rect r;GetWindowRect(h,out r);return r;}
}
'@

function Wait-Wizard {
  for ($attempt=0; $attempt -lt 100; $attempt++) {
    $window = [SpaceWindow]::Wizard()
    if ($window -ne [IntPtr]::Zero -and [SpaceWindow]::Button($window,'Install') -ne [IntPtr]::Zero) { return $window }
    Start-Sleep -Milliseconds 200
  }
  throw 'Installer did not show its computer-check page'
}
function Kill-Wizard($window) {
  if ($window -ne [IntPtr]::Zero) { Stop-Process -Id ([SpaceWindow]::Pid($window)) -Force -ErrorAction SilentlyContinue }
}
foreach ($scenario in @('low','unknown','changed','ready')) {
  $target = Join-Path $root "interactive space $scenario"
  $trigger = Join-Path $root 'space-changed.flag'
  Remove-Item $trigger -ErrorAction SilentlyContinue
  $p = Start-Process $installerPath -ArgumentList @('/NORESTART',"/DIR=`"$target`"", "/SPACEFIXTURE=$scenario", "/SPACECHANGE=`"$trigger`"") -PassThru
  $window = [IntPtr]::Zero
  try {
    $window = Wait-Wizard
    $install = [SpaceWindow]::Button($window,'Install')
    $text = ([SpaceWindow]::Children($window) | ForEach-Object { [SpaceWindow]::Text($_) }) -join "`n"
    if ($text -notmatch 'Check your computer' -or $text -notmatch 'Temporary setup and startup' -or $text -notmatch 'Private data headroom') { throw "Computer check details missing: $text" }
    if ([SpaceWindow]::Button($window,'Change location...') -eq [IntPtr]::Zero -or [SpaceWindow]::Button($window,'Check again') -eq [IntPtr]::Zero) { throw 'Location and refresh controls missing' }
    if ($scenario -in @('low','unknown')) {
      if ([SpaceWindow]::IsWindowEnabled($install)) { throw "$scenario space left Install enabled" }
    } else {
      if (-not [SpaceWindow]::IsWindowEnabled($install)) { throw 'Valid space blocked Install' }
      if ($scenario -eq 'changed') {
        Set-Content $trigger 'now-low'
        [SpaceWindow]::Click($install)
        Start-Sleep -Milliseconds 300
        if ([SpaceWindow]::IsWindowEnabled($install) -or (Test-Path (Join-Path $target 'HomeServer.exe'))) { throw 'Free-space change bypassed final checks' }
      } else {
        $bounds=[SpaceWindow]::Bounds($window)
        $bitmap=[Drawing.Bitmap]::new(($bounds.Right-$bounds.Left),($bounds.Bottom-$bounds.Top))
        $graphics=[Drawing.Graphics]::FromImage($bitmap)
        $graphics.CopyFromScreen($bounds.Left,$bounds.Top,0,0,$bitmap.Size)
        $bitmap.Save((Join-Path $root 'computer-check.png'))
        $graphics.Dispose(); $bitmap.Dispose()
        [SpaceWindow]::Click($install)
        for ($attempt=0; $attempt -lt 150; $attempt++) {
          $finish=[SpaceWindow]::Button($window,'Finish')
          if ($finish -ne [IntPtr]::Zero) { [SpaceWindow]::Click($finish); break }
          Start-Sleep -Milliseconds 200
        }
        if (-not (Test-Path (Join-Path $target 'HomeServer.exe'))) { throw 'One-page installation did not write to the selected folder' }
      }
    }
  } finally { Kill-Wizard $window }
}
if ((Get-Content $sentinel -Raw).Trim() -ne 'private-state') { throw 'Private data was changed' }
Write-Host 'PASS native installer UI: ready/blocked/unknown, location options, final recheck, one-page install and private data preservation'
