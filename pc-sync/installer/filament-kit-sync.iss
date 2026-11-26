; Inno Setup 6 script: per-user installer for the Spoolwarden Slicer Sync (no admin rights needed).
; Build (from the repo root, after `pyinstaller --noconfirm pc-sync/fksync.spec`):
;   ISCC /DAppVersion=0.2.0 pc-sync\installer\filament-kit-sync.iss
; Output: dist\filament-kit-sync-setup-<AppVersion>.exe
; Relative paths below are relative to this file's folder.

#ifndef AppVersion
  #error Pass the version: ISCC /DAppVersion=<x.y.z> filament-kit-sync.iss
#endif
#ifndef DistDir
  #define DistDir "..\..\dist\filament-kit-sync"
#endif
#ifndef OutDir
  #define OutDir "..\..\dist"
#endif

#define AppName "Spoolwarden Slicer Sync"
#define ExeName "filament-kit-sync.exe"
#define TaskExeName "filament-kit-sync-task.exe"
#define ConfigExeName "filament-kit-sync-config.exe"

[Setup]
; Fixed AppId: every version upgrades the same install. Never change it.
AppId={{B7461DED-A0F4-46B3-B2C9-C8F0EF762ABB}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Spoolwarden
VersionInfoVersion={#AppVersion}
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\filament-kit-sync
DisableDirPage=yes
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
UsePreviousAppDir=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutDir}
OutputBaseFilename=filament-kit-sync-setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#ExeName}
; The task exe may be mid-run during an upgrade: close it rather than fail on a locked file.
CloseApplications=yes
RestartApplications=no
; {app} is added to the per-user PATH (so `filament-kit-sync status` works in a new terminal):
; tell Explorer to reload the environment after install and uninstall.
ChangesEnvironment=yes

[Files]
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "install_task.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "uninstall_task.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\THIRD-PARTY.md"; DestDir: "{app}"; Flags: ignoreversion

[InstallDelete]
; Files of an older build that this build no longer has.
Type: filesandordirs; Name: "{app}\_internal"

[Registry]
; Per-user PATH: append {app} once (NeedsAddPath); the uninstaller removes just that entry (RemovePath in [Code]).
Root: HKCU; Subkey: "Environment"; ValueType: expandsz; ValueName: "Path"; ValueData: "{olddata};{app}"; Check: NeedsAddPath(ExpandConstant('{app}')); Flags: preservestringtype

[Icons]
; cmd /k keeps the window open so the result of pair/status stays readable.
Name: "{group}\Pair Slicer Sync"; Filename: "{cmd}"; Parameters: "/k """"{app}\{#ExeName}"" pair"""; WorkingDir: "{app}"
Name: "{group}\Slicer Sync status"; Filename: "{cmd}"; Parameters: "/k """"{app}\{#ExeName}"" status"""; WorkingDir: "{app}"
Name: "{group}\Configure Slicer Sync"; Filename: "{app}\{#ConfigExeName}"; WorkingDir: "{app}"
Name: "{userdesktop}\Configure Slicer Sync"; Filename: "{app}\{#ConfigExeName}"; WorkingDir: "{app}"
Name: "{group}\Uninstall Spoolwarden Slicer Sync"; Filename: "{uninstallexe}"

[Run]
; 1) The scheduled task is registered in [Code] (CurStepChanged), where its exit code is checked.
; 2) Pair this PC with the Hub, in a console (paste the link from 'sudo spoolwarden pair --pc').
Filename: "{cmd}"; Parameters: "/k """"{app}\{#ExeName}"" pair"""; WorkingDir: "{app}"; Description: "Pair this PC with the Hub now"; Flags: postinstall nowait skipifsilent

[UninstallRun]
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File ""{app}\uninstall_task.ps1"""; RunOnceId: "RemoveScheduledTask"; Flags: runhidden waituntilterminated

[Code]
var
  SlicerPathsPage: TWizardPage;
  SlicerList: TNewCheckListBox;
  EntryKinds, EntryLabels, EntryPaths: array of string;
  EntryCustom: array of Boolean;

procedure AddSlicerEntry(Kind, LabelText, PathVal: string; Checked, IsCustom: Boolean);
var
  I, N: Integer;
  NormPath: string;
begin
  NormPath := RemoveBackslashUnlessRoot(Trim(PathVal));
  if NormPath = '' then
    exit;
  for I := 0 to GetArrayLength(EntryPaths) - 1 do
  begin
    if CompareText(EntryPaths[I], NormPath) = 0 then
      exit;
  end;
  N := GetArrayLength(EntryPaths);
  SetArrayLength(EntryKinds, N + 1);
  SetArrayLength(EntryLabels, N + 1);
  SetArrayLength(EntryPaths, N + 1);
  SetArrayLength(EntryCustom, N + 1);
  EntryKinds[N] := Kind;
  EntryLabels[N] := LabelText;
  EntryPaths[N] := NormPath;
  EntryCustom[N] := IsCustom;
  SlicerList.AddCheckBox(LabelText + ' - ' + NormPath, '', 0, Checked, True, False, False, nil);
end;

procedure AutoDetectSlicerPaths;
var
  Candidate, InstallDir: string;
begin
  Candidate := ExpandConstant('{userappdata}\BambuStudio');
  if DirExists(Candidate) then
    AddSlicerEntry('bambu', 'BambuStudio', Candidate, True, False);

  Candidate := ExpandConstant('{userappdata}\BambuStudioBeta');
  if DirExists(Candidate) then
    AddSlicerEntry('bambu', 'BambuStudioBeta', Candidate, True, False);

  Candidate := ExpandConstant('{userappdata}\OrcaSlicer');
  if DirExists(Candidate) then
    AddSlicerEntry('orca', 'OrcaSlicer', Candidate, True, False);

  if RegQueryStringValue(HKEY_LOCAL_MACHINE, 'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\OrcaSlicer', 'InstallLocation', InstallDir) then
  begin
    InstallDir := RemoveBackslashUnlessRoot(Trim(InstallDir));
    if (InstallDir <> '') and FileExists(InstallDir + '\orca-slicer.exe') then
      AddSlicerEntry('orca_install', 'Orca Slicer Install', InstallDir, True, False);
  end;

  if RegQueryStringValue(HKEY_CURRENT_USER, 'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\OrcaSlicer', 'InstallLocation', InstallDir) then
  begin
    InstallDir := RemoveBackslashUnlessRoot(Trim(InstallDir));
    if (InstallDir <> '') and FileExists(InstallDir + '\orca-slicer.exe') then
      AddSlicerEntry('orca_install', 'Orca Slicer Install', InstallDir, True, False);
  end;

  Candidate := ExpandConstant('{pf}\OrcaSlicer');
  if FileExists(Candidate + '\orca-slicer.exe') then
    AddSlicerEntry('orca_install', 'Orca Slicer Install', Candidate, True, False);

  Candidate := ExpandConstant('{pf32}\OrcaSlicer');
  if FileExists(Candidate + '\orca-slicer.exe') then
    AddSlicerEntry('orca_install', 'Orca Slicer Install', Candidate, True, False);
end;

procedure OnAutoDetectClick(Sender: TObject);
begin
  AutoDetectSlicerPaths;
end;

procedure OnModifyClick(Sender: TObject);
var
  Idx: Integer;
  Dir: string;
begin
  Idx := SlicerList.ItemIndex;
  if (Idx < 0) or (Idx >= GetArrayLength(EntryPaths)) then
    exit;
  Dir := EntryPaths[Idx];
  if BrowseForFolder('Select slicer folder:', Dir, False) then
  begin
    Dir := RemoveBackslashUnlessRoot(Trim(Dir));
    EntryPaths[Idx] := Dir;
    EntryCustom[Idx] := True;
    SlicerList.ItemCaption[Idx] := EntryLabels[Idx] + ' - ' + Dir;
  end;
end;

procedure OnAddClick(Sender: TObject);
var
  Dir, Kind: string;
begin
  Dir := ExpandConstant('{userappdata}');
  if BrowseForFolder('Select additional Bambu Studio or Orca Slicer folder:', Dir, False) then
  begin
    Dir := RemoveBackslashUnlessRoot(Trim(Dir));
    if FileExists(Dir + '\orca-slicer.exe') then
      Kind := 'orca_install'
    else if Pos('orca', Lowercase(Dir)) > 0 then
      Kind := 'orca'
    else
      Kind := 'bambu';
    AddSlicerEntry(Kind, ExtractFileName(Dir), Dir, True, True);
  end;
end;

procedure OnRemoveClick(Sender: TObject);
var
  Idx, I, N: Integer;
  CheckedStates: array of Boolean;
begin
  Idx := SlicerList.ItemIndex;
  N := GetArrayLength(EntryPaths);
  if (Idx < 0) or (Idx >= N) then
    exit;
  SetArrayLength(CheckedStates, N);
  for I := 0 to N - 1 do
    CheckedStates[I] := SlicerList.Checked[I];
  for I := Idx to N - 2 do
  begin
    EntryKinds[I] := EntryKinds[I + 1];
    EntryLabels[I] := EntryLabels[I + 1];
    EntryPaths[I] := EntryPaths[I + 1];
    EntryCustom[I] := EntryCustom[I + 1];
    CheckedStates[I] := CheckedStates[I + 1];
  end;
  SetArrayLength(EntryKinds, N - 1);
  SetArrayLength(EntryLabels, N - 1);
  SetArrayLength(EntryPaths, N - 1);
  SetArrayLength(EntryCustom, N - 1);
  SlicerList.Items.Clear;
  for I := 0 to N - 2 do
    SlicerList.AddCheckBox(EntryLabels[I] + ' - ' + EntryPaths[I], '', 0, CheckedStates[I], True, False, False, nil);
end;

procedure InitializeWizard;
var
  BtnAutoDetect, BtnModify, BtnAdd, BtnRemove: TNewButton;
  BtnTop, BtnWidth, BtnHeight, Gap: Integer;
begin
  SlicerPathsPage := CreateCustomPage(wpWelcome, 'Slicer Locations', 'Select, modify, or add the Bambu Studio and Orca Slicer folders to sync.');

  SlicerList := TNewCheckListBox.Create(SlicerPathsPage);
  SlicerList.Parent := SlicerPathsPage.Surface;
  SlicerList.Left := 0;
  SlicerList.Top := 0;
  SlicerList.Width := SlicerPathsPage.SurfaceWidth;
  SlicerList.Height := SlicerPathsPage.SurfaceHeight - ScaleY(36);
  SlicerList.Anchors := [akLeft, akTop, akRight, akBottom];

  BtnTop := SlicerList.Top + SlicerList.Height + ScaleY(8);
  BtnHeight := ScaleY(24);
  BtnWidth := ScaleX(92);
  Gap := ScaleX(8);

  BtnAutoDetect := TNewButton.Create(SlicerPathsPage);
  BtnAutoDetect.Parent := SlicerPathsPage.Surface;
  BtnAutoDetect.Left := 0;
  BtnAutoDetect.Top := BtnTop;
  BtnAutoDetect.Width := BtnWidth;
  BtnAutoDetect.Height := BtnHeight;
  BtnAutoDetect.Caption := 'Auto-detect';
  BtnAutoDetect.Anchors := [akLeft, akBottom];
  BtnAutoDetect.OnClick := @OnAutoDetectClick;

  BtnModify := TNewButton.Create(SlicerPathsPage);
  BtnModify.Parent := SlicerPathsPage.Surface;
  BtnModify.Left := BtnAutoDetect.Left + BtnWidth + Gap;
  BtnModify.Top := BtnTop;
  BtnModify.Width := BtnWidth;
  BtnModify.Height := BtnHeight;
  BtnModify.Caption := 'Modify...';
  BtnModify.Anchors := [akLeft, akBottom];
  BtnModify.OnClick := @OnModifyClick;

  BtnAdd := TNewButton.Create(SlicerPathsPage);
  BtnAdd.Parent := SlicerPathsPage.Surface;
  BtnAdd.Left := BtnModify.Left + BtnWidth + Gap;
  BtnAdd.Top := BtnTop;
  BtnAdd.Width := BtnWidth;
  BtnAdd.Height := BtnHeight;
  BtnAdd.Caption := 'Add...';
  BtnAdd.Anchors := [akLeft, akBottom];
  BtnAdd.OnClick := @OnAddClick;

  BtnRemove := TNewButton.Create(SlicerPathsPage);
  BtnRemove.Parent := SlicerPathsPage.Surface;
  BtnRemove.Left := BtnAdd.Left + BtnWidth + Gap;
  BtnRemove.Top := BtnTop;
  BtnRemove.Width := BtnWidth;
  BtnRemove.Height := BtnHeight;
  BtnRemove.Caption := 'Remove';
  BtnRemove.Anchors := [akLeft, akBottom];
  BtnRemove.OnClick := @OnRemoveClick;

  AutoDetectSlicerPaths;
end;

function JsonEscape(S: string): string;
var
  I: Integer;
  Ch: string;
begin
  Result := '';
  for I := 1 to Length(S) do
  begin
    Ch := S[I];
    if Ch = '\' then
      Result := Result + '\\'
    else if Ch = '"' then
      Result := Result + '\"'
    else
      Result := Result + Ch;
  end;
end;

procedure SaveSlicerPaths;
var
  I, N, ResultCode: Integer;
  Json, JsonFile, EnabledStr, CustomStr: string;
begin
  N := GetArrayLength(EntryPaths);
  Json := '[';
  for I := 0 to N - 1 do
  begin
    if I > 0 then
      Json := Json + ',';
    if SlicerList.Checked[I] then
      EnabledStr := 'true'
    else
      EnabledStr := 'false';
    if EntryCustom[I] then
      CustomStr := 'true'
    else
      CustomStr := 'false';
    Json := Json + '{"kind":"' + JsonEscape(EntryKinds[I]) + '","label":"' + JsonEscape(EntryLabels[I]) +
      '","path":"' + JsonEscape(EntryPaths[I]) + '","enabled":' + EnabledStr + ',"custom":' + CustomStr + '}';
  end;
  Json := Json + ']';

  JsonFile := ExpandConstant('{tmp}\slicer-paths.json');
  if SaveStringToFile(JsonFile, Json, False) then
  begin
    ResultCode := -1;
    Exec(ExpandConstant('{app}\{#ConfigExeName}'), '--apply-paths "' + JsonFile + '"',
      '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    DeleteFile(JsonFile);
  end;
end;

// True when Dir is not yet an entry of the per-user PATH (case-insensitive, whole entries only).
function NeedsAddPath(Dir: string): Boolean;
var
  Paths: string;
begin
  if not RegQueryStringValue(HKEY_CURRENT_USER, 'Environment', 'Path', Paths) then
  begin
    Result := True;
    exit;
  end;
  Result := Pos(';' + Uppercase(Dir) + ';', ';' + Uppercase(Paths) + ';') = 0;
end;

// Removes the entry Dir from the per-user PATH, leaving every other entry as it was.
procedure RemovePath(Dir: string);
var
  Paths, Needle: string;
  P: Integer;
begin
  if not RegQueryStringValue(HKEY_CURRENT_USER, 'Environment', 'Path', Paths) then
    exit;
  Paths := ';' + Paths + ';';
  Needle := ';' + Uppercase(Dir) + ';';
  P := Pos(Needle, Uppercase(Paths));
  if P = 0 then
    exit;
  Delete(Paths, P, Length(Needle) - 1);  // drop ";<Dir>", keep the following ";"
  Paths := Copy(Paths, 2, Length(Paths) - 2);  // drop the ";" added at each end above
  if (Paths = '') or (Paths = ';') then
    RegDeleteValue(HKEY_CURRENT_USER, 'Environment', 'Path')
  else
    RegWriteExpandStringValue(HKEY_CURRENT_USER, 'Environment', 'Path', Paths);
end;

// Registers the scheduled task (every 10 minutes and at logon, as the current user). A failure is shown, with the
// way to finish by hand; the install itself stays (the files are there, and the task can be registered later).
procedure RegisterTask;
var
  ResultCode: Integer;
  Script, Exe: string;
begin
  WizardForm.StatusLabel.Caption := 'Registering the scheduled task...';
  Script := ExpandConstant('{app}\install_task.ps1');
  Exe := ExpandConstant('{app}\{#TaskExeName}');
  ResultCode := -1;
  if not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
      '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + Script + '" -ExePath "' + Exe + '"',
      '', SW_HIDE, ewWaitUntilTerminated, ResultCode) or (ResultCode <> 0) then
  begin
    Log('install_task.ps1 failed, exit code ' + IntToStr(ResultCode));
    SuppressibleMsgBox('The scheduled task that runs Slicer Sync every 10 minutes could not be registered (exit code ' +
      IntToStr(ResultCode) + '). Slicer Sync will not run by itself until it is.' + #13#10 + #13#10 +
      'To finish, run this in PowerShell:' + #13#10 + 'powershell -ExecutionPolicy Bypass -File "' + Script +
      '" -ExePath "' + Exe + '"' + #13#10 + 'or run this installer again.', mbError, MB_OK, IDOK);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    SaveSlicerPaths;
    RegisterTask;
  end;
end;

// Reads the paired device id from config.xml (or legacy config.json) before data deletion.
function ReadDeviceId(Data: string): string;
var
  Raw: AnsiString;
  S: string;
  P, Q: Integer;
begin
  Result := '';
  if FileExists(Data + '\config.xml') and LoadStringFromFile(Data + '\config.xml', Raw) then
  begin
    S := String(Raw);
    P := Pos('<device>', S);
    if P > 0 then
    begin
      Delete(S, 1, P + Length('<device>') - 1);
      Q := Pos('</device>', S);
      if Q > 0 then
      begin
        Result := Trim(Copy(S, 1, Q - 1));
        if Result <> '' then
          exit;
      end;
    end;
  end;
  if FileExists(Data + '\config.json') and LoadStringFromFile(Data + '\config.json', Raw) then
  begin
    S := String(Raw);
    P := Pos('"device"', S);
    if P > 0 then
    begin
      Delete(S, 1, P + Length('"device"') - 1);
      P := Pos(':', S);
      if P > 0 then
      begin
        Delete(S, 1, P);
        S := Trim(S);
        if (Length(S) >= 2) and (S[1] = '"') then
        begin
          Delete(S, 1, 1);
          Q := Pos('"', S);
          if Q > 0 then
            Result := Trim(Copy(S, 1, Q - 1));
        end;
      end;
    end;
  end;
end;

// Asks whether to delete the sync's data (pairing token, settings, log, preset backups) in
// %LOCALAPPDATA%\filament-kit-sync, as `uninstall.sh --purge` does on Linux. The default is to keep it (a reinstall
// pairs again without a new link); a silent uninstall keeps it. When a device id is present, the unpair hint is shown.
procedure AskDeleteData;
var
  Data, DeviceId: string;
  Wipe: Boolean;
begin
  Data := ExpandConstant('{localappdata}\filament-kit-sync');
  if not DirExists(Data) then
    exit;
  DeviceId := ReadDeviceId(Data);
  Wipe := SuppressibleMsgBox('Also delete the sync''s data (pairing token, settings, log and the Bambu Studio and ' +
    'Orca Slicer preset backups) in' + #13#10 + Data + ' ?' + #13#10 + #13#10 +
    'Choose No to keep it; installing again then needs no new pairing.', mbConfirmation, MB_YESNO or MB_DEFBUTTON2,
    IDNO) = IDYES;
  if Wipe then
    DelTree(Data, True, True, True);
  if DeviceId <> '' then
    SuppressibleMsgBox('This PC is still paired with the Hub. To free its place for another PC, run on the Hub:' + #13#10 +
      'sudo spoolwarden unpair ' + DeviceId, mbInformation, MB_OK, IDOK);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    RemovePath(ExpandConstant('{app}'))
  else if CurUninstallStep = usPostUninstall then
    AskDeleteData;
end;
