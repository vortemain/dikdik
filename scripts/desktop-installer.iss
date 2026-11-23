#ifndef AppVersion
  #define AppVersion "0.2.0"
#endif
#define Publisher "Vorte by Lijohtech Developers"
[Setup]
AppId=Dikdik.Desktop
AppName=Dikdik
AppVersion={#AppVersion}
AppPublisher={#Publisher}
AppPublisherURL=https://github.com/vortemain/dikdik
AppSupportURL=https://github.com/vortemain/dikdik/issues
AppUpdatesURL=https://github.com/vortemain/dikdik/releases
VersionInfoCompany={#Publisher}
VersionInfoProductName=Dikdik
VersionInfoVersion={#AppVersion}
VersionInfoCopyright=(c) {#Publisher}
; Release builds pass /DSign=1 and /Sdikdiksign=... so the setup and its uninstaller are signed too.
#ifdef Sign
SignTool=dikdiksign
SignedUninstaller=yes
#endif
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
; Updates: close whatever still holds Dikdik's files (the app, its browser or a preview server),
; never reopen them from the installer (the updater opens the new Dikdik itself) and never
; run two installers at once.
CloseApplications=force
CloseApplicationsFilter=*.exe,*.dll,*.pyd
RestartApplications=no
SetupMutex=DikdikSetup

[Files]
Source: "..\dist\desktop\Dikdik\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Dikdik"; Filename: "{app}\Dikdik.exe"; AppUserModelID: "Dikdik.Desktop"
Name: "{autodesktop}\Dikdik"; Filename: "{app}\Dikdik.exe"; Tasks: desktopicon; AppUserModelID: "Dikdik.Desktop"

[Tasks]
Name: desktopicon; Description: "Create a desktop shortcut"; Flags: unchecked

[Run]
Filename: "{app}\Dikdik.exe"; Description: "Open Dikdik"; Flags: nowait postinstall skipifsilent
