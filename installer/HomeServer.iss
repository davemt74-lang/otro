#define MyAppName "HomeServer"
#define MyAppVersion "0.9.0"
#define MyAppPublisher "HomeServer"
#define MyAppExeName "HomeServer.exe"

[Setup]
AppId={{F94F980E-7B18-4FA3-A9B8-75A2EDE04777}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\HomeServer
DefaultGroupName=HomeServer
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=HomeServerSetup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#MyAppExeName}
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked
Name: "startup"; Description: "Start HomeServer when I sign in to Windows"; GroupDescription: "Startup:"; Flags: unchecked

[Files]
Source: "..\dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\HomeServer"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\HomeServer"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
Name: "{userstartup}\HomeServer"; Filename: "{app}\{#MyAppExeName}"; Tasks: startup

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch HomeServer"; Flags: nowait postinstall skipifsilent
