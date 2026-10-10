; Inno Setup script for AI Video Studio.
;
; Build:  "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" /DMyAppVersion=3.0.0 packaging\installer.iss
; Output: dist\installer\AIVideoStudio-<version>-setup.exe
;
; Requires dist\AIVideoStudio\ to exist, i.e. run PyInstaller first.

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif
#include "model.iss"
; Where the built app is. A test build can point somewhere else.
#ifndef SourceDir
  #define SourceDir "..\dist\AIVideoStudio"
#endif
; A test build (/DTestBuild) installs beside the real app instead of over it.
#ifdef TestBuild
  #define MyAppName    "AI Video Studio Test"
  #define MyAppId      "{{7C0B6E0A-3D0B-4B0E-9C1B-5E7B0C2D9A11}"
#else
  #define MyAppName    "AI Video Studio"
  #define MyAppId      "{{B109BA40-FBF4-4475-852D-8CE0284DC108}"
#endif
#define MyAppExeName   "AIVideoStudio.exe"
#define MyAppPublisher "AI Video Studio"
#define MyAppURL       "https://github.com/bodrumundenizi-beep/free-ai-video-generator"

[Setup]
; Never change this GUID. Changing it makes an upgrade install alongside the old
; version instead of replacing it.
AppId={#MyAppId}
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
; Always ask where to install. On an update the page shows the folder the app is
; already in, so pressing Next keeps it there.
DisableDirPage=no

LicenseFile=..\LICENSE
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName} {#MyAppVersion}

OutputDir=..\dist\installer
OutputBaseFilename=AIVideoStudio-{#MyAppVersion}-setup
; /DFastBuild skips compression, for throwaway test installers only.
#ifdef FastBuild
Compression=none
SolidCompression=no
#else
Compression=lzma2/max
SolidCompression=yes
#endif
WizardStyle=modern
; An update started from inside the app runs this installer silently while the
; app is closing. Close a copy that is still shutting down so its files can be
; replaced, and leave reopening it to the [Run] entry below.
CloseApplications=yes
RestartApplications=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; \
  GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; \
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
; "Update now" inside the app passes /RELAUNCH=1: the entry above is skipped in
; a silent install, and an update that ends with nothing on screen looks broken.
Filename: "{app}\{#MyAppExeName}"; Flags: nowait; Check: RelaunchAsked

[UninstallDelete]
; Scratch space only, never user data.
Type: filesandordirs; Name: "{localappdata}\Temp\AIVideoStudio"
; The AI models: 2.9 GB that only this app uses, and can fetch again.
; (Not for a test build: it shares the real app's model and must not delete it.)
#ifndef TestBuild
Type: filesandordirs; Name: "{localappdata}\AIVideoStudio\models"
Type: dirifempty; Name: "{localappdata}\AIVideoStudio"
#endif

[Code]
function RelaunchAsked(): Boolean;
begin
  Result := ExpandConstant('{param:RELAUNCH|0}') = '1';
end;

// ---- The AI models: the Smart writer and the voice ----------------------------
// Together they are 2.9 GB, so they are not inside this installer: they are
// downloaded here, once, and kept in the user's profile where app updates leave
// them alone. If a download fails or is cancelled the install still finishes,
// and the app fetches what is missing the first time it is needed.
var
  ModelPage: TDownloadWizardPage;
  Fetched: array[0..2] of Boolean;

function ModelDir(): String;
begin
  Result := ExpandConstant('{localappdata}\AIVideoStudio\models');
end;

function ModelName(Index: Integer): String;
begin
  case Index of
    0: Result := '{#ModelFile}';
    1: Result := '{#VoiceFile}';
  else
    Result := '{#SpeakersFile}';
  end;
end;

function ModelPresent(Index: Integer): Boolean;
var
  Size, Wanted: Int64;
begin
  // Same test as the app: the whole file is there. Its contents were checked
  // against the SHA-256 when it was downloaded, by this installer or by the app.
  case Index of
    0: Wanted := StrToInt64('{#ModelSize}');
    1: Wanted := StrToInt64('{#VoiceSize}');
  else
    Wanted := StrToInt64('{#SpeakersSize}');
  end;
  Result := FileSize64(ModelDir() + '\' + ModelName(Index), Size) and (Size = Wanted);
end;

procedure InitializeWizard();
begin
  ModelPage := CreateDownloadPage(
    'Downloading the AI models',
    'The open-source AI that writes scripts (2.5 GB) and the voice that reads them '
    + '(0.4 GB) run on your PC and are downloaded once. '
    + 'You can cancel: the app will download them later.', nil);
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Index: Integer;
  Wanted: array[0..2] of Boolean;
begin
  Result := True;
  if CurPageID <> wpReady then
    exit;
  ModelPage.Clear;
  for Index := 0 to 2 do
  begin
    Fetched[Index] := False;
    Wanted[Index] := not ModelPresent(Index);
  end;
  // The voice first: it is small, and every video needs it.
  if Wanted[1] then
    ModelPage.Add('{#VoiceUrl}', '{#VoiceFile}', '{#VoiceSha256}');
  if Wanted[2] then
    ModelPage.Add('{#SpeakersUrl}', '{#SpeakersFile}', '{#SpeakersSha256}');
  if Wanted[0] then
    ModelPage.Add('{#ModelUrl}', '{#ModelFile}', '{#ModelSha256}');
  if not (Wanted[0] or Wanted[1] or Wanted[2]) then
    exit;
  ModelPage.Show;
  try
    try
      ModelPage.Download;
    except
      if ModelPage.AbortedByUser then
        Log('Model download cancelled.')
      else
        Log('Model download failed: ' + GetExceptionMessage);
      SuppressibleMsgBox(
        'Not everything was downloaded.' #13#10 #13#10
        + 'Setup will finish anyway. AI Video Studio will download what is missing '
        + 'the first time it is needed.',
        mbInformation, MB_OK, IDOK);
    end;
    // A file that arrived whole is kept even if a later one failed.
    for Index := 0 to 2 do
      Fetched[Index] := Wanted[Index]
        and FileExists(ExpandConstant('{tmp}\') + ModelName(Index));
  finally
    ModelPage.Hide;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Index: Integer;
  Source, Target: String;
begin
  if CurStep <> ssPostInstall then
    exit;
  for Index := 0 to 2 do
    if Fetched[Index] then
    begin
      Source := ExpandConstant('{tmp}\') + ModelName(Index);
      Target := ModelDir() + '\' + ModelName(Index);
      ForceDirectories(ModelDir());
      DeleteFile(Target);
      // Same drive in almost every setup, so this is a rename, not a 2.5 GB copy.
      if not RenameFile(Source, Target) then
        if not FileCopy(Source, Target, False) then
          Log('Could not put ' + ModelName(Index) + ' in ' + ModelDir());
    end;
end;

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
