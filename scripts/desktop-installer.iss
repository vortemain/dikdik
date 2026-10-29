#ifndef AppVersion
  #define AppVersion "0.2.0"
#endif
[Setup]
AppId=Dikdik.Desktop
AppName=Dikdik
AppVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\Dikdik
DefaultGroupName=Dikdik
PrivilegesRequired=lowest
OutputDir=..\dist\installers
OutputBaseFilename=Dikdik-{#AppVersion}-Windows-x64-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\dikdik\web\icon.ico
UninstallDisplayIcon={app}\Dikdik.exe

[Files]
Source: "..\dist\desktop\Dikdik\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Dikdik"; Filename: "{app}\Dikdik.exe"; AppUserModelID: "Dikdik.Desktop"
Name: "{autodesktop}\Dikdik"; Filename: "{app}\Dikdik.exe"; Tasks: desktopicon; AppUserModelID: "Dikdik.Desktop"

[Tasks]
Name: desktopicon; Description: "Create a desktop shortcut"; Flags: unchecked

[Run]
Filename: "{app}\Dikdik.exe"; Description: "Open Dikdik"; Flags: nowait postinstall skipifsilent
