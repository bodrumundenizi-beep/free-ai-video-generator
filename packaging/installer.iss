; Inno Setup script for AI Video Studio.
;
; Build:  "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" /DMyAppVersion=3.0.0 packaging\installer.iss
; Output: dist\installer\AIVideoStudio-<version>-setup.exe
;
; Requires dist\AIVideoStudio\ to exist, i.e. run PyInstaller first.

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif
#define MyAppName      "AI Video Studio"
#define MyAppExeName   "AIVideoStudio.exe"
#define MyAppPublisher "AI Video Studio"
#define MyAppURL       "https://github.com/bodrumundenizi-beep/free-ai-video-generator"

[Setup]
; Never change this GUID. Changing it makes an upgrade install alongside the old
; version instead of replacing it.
AppId={{B109BA40-FBF4-4475-852D-8CE0284DC108}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
VersionInfoVersion={#MyAppVersion}

; Per-user install: no UAC prompt, and it lands somewhere writable, which is
; what an unsigned installer wants on both counts. {autopf} resolves to
; {localappdata}\Programs when PrivilegesRequired=lowest.
PrivilegesRequired=lowest
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
DisableDirPage=auto

LicenseFile=..\LICENSE
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName} {#MyAppVersion}

OutputDir=..\dist\installer
OutputBaseFilename=AIVideoStudio-{#MyAppVersion}-setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; \
  GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\AIVideoStudio\*"; DestDir: "{app}"; \
  Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\THIRD-PARTY-LICENSES.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; AppUserModelID must match APP_MODEL_ID in src/ai_video_studio.py, or a pinned
; taskbar shortcut will not group with the running window.
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; \
  AppUserModelID: "AIVideoStudio.Desktop.3"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; \
  Tasks: desktopicon; AppUserModelID: "AIVideoStudio.Desktop.3"

[Run]
Filename: "{app}\{#MyAppExeName}"; \
  Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; \
  Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Scratch space only, never user data.
Type: filesandordirs; Name: "{localappdata}\Temp\AIVideoStudio"

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  SettingsDir: String;
begin
  // Do not silently delete the settings: they hold the user's Pexels API key
  // and their script. Ask, default to No, and let a silent uninstall keep them.
  if CurUninstallStep = usPostUninstall then
  begin
    SettingsDir := ExpandConstant('{userappdata}\AIVideoStudio');
    if DirExists(SettingsDir) then
      if SuppressibleMsgBox(
           'Also remove your AI Video Studio settings?' #13#10 #13#10
           + 'This deletes your saved Pexels API key and your script.',
           mbConfirmation, MB_YESNO, IDNO) = IDYES then
        DelTree(SettingsDir, True, True, True);
  end;
end;
