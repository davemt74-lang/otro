#define MyAppName "HomeServer"
#define MyAppVersion "2.4"
#define MyAppPublisher "HomeServer"
#define MyAppExeName "HomeServer.exe"
#ifdef CORE_UPDATE
#include "CoreSpaceBudget.iss"
#else
#include "SpaceBudget.iss"
#endif

[Setup]
#ifdef PREFLIGHT_TEST_BUILD
AppId={{3B921A76-0C71-4963-BF03-DA187A0455F6}
Uninstallable=no
CreateUninstallRegKey=no
#else
AppId={{F94F980E-7B18-4FA3-A9B8-75A2EDE04777}
#endif
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\HomeServer
DefaultGroupName=HomeServer
PrivilegesRequired=lowest
OutputDir=..\dist
#ifdef CORE_UPDATE
OutputBaseFilename=HomeServerCoreSetup
#else
OutputBaseFilename=HomeServerSetup
#endif
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
WizardSizePercent=120,140
UninstallDisplayIcon={app}\{#MyAppExeName}
CloseApplications=yes
RestartApplications=no
UsePreviousTasks=yes
SetupLogging=yes
DisableWelcomePage=yes
DisableDirPage=yes
DisableReadyPage=yes
ArchitecturesAllowed=x64compatible
MinVersion=10.0

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked
Name: "startup"; Description: "Start HomeServer when I sign in to Windows"; GroupDescription: "Startup:"; Flags: unchecked

[Files]
Source: "..\dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
#ifndef CORE_UPDATE
Source: "..\dist\tools\ffmpeg\*"; DestDir: "{app}\tools\ffmpeg"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\dist\tools\browser\*"; DestDir: "{app}\tools\browser"; Flags: ignoreversion recursesubdirs createallsubdirs

#endif

[Icons]
Name: "{autoprograms}\HomeServer"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\HomeServer"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
#ifndef PREFLIGHT_TEST_BUILD
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "HomeServer"; ValueData: """{app}\{#MyAppExeName}"" --background"; Flags: uninsdeletevalue; Tasks: startup
#endif

[Run]
#ifndef PREFLIGHT_TEST_BUILD
Filename: "{app}\{#MyAppExeName}"; Description: "Launch HomeServer"; Flags: nowait postinstall skipifsilent
#endif

[UninstallRun]
Filename: "{cmd}"; Parameters: "/C reg delete HKCU\Software\Microsoft\Windows\CurrentVersion\Run /v HomeServer /f >nul 2>&1"; Flags: runhidden; RunOnceId: "HomeServerStartupCleanup"

[Code]
#include "SpaceCheck.iss"
