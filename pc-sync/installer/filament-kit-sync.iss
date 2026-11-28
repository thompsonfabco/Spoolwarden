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

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Icons]
; cmd /k keeps the window open so the result of pair/status stays readable.
Name: "{group}\Pair Slicer Sync"; Filename: "{cmd}"; Parameters: "/k """"{app}\{#ExeName}"" pair"""; WorkingDir: "{app}"
Name: "{group}\Slicer Sync status"; Filename: "{cmd}"; Parameters: "/k """"{app}\{#ExeName}"" status"""; WorkingDir: "{app}"
Name: "{group}\Configure Slicer Sync"; Filename: "{app}\{#ConfigExeName}"; WorkingDir: "{app}"
Name: "{userdesktop}\Configure Slicer Sync"; Filename: "{app}\{#ConfigExeName}"; WorkingDir: "{app}"; Tasks: desktopicon
Name: "{group}\Uninstall Spoolwarden Slicer Sync"; Filename: "{uninstallexe}"

[Run]
; 1) The scheduled task is registered in [Code] (CurStepChanged), where its exit code is checked.
; 2) Pair this PC with the Hub, in a console (paste the link from 'sudo spoolwarden pair --pc').
Filename: "{cmd}"; Parameters: "/k """"{app}\{#ExeName}"" pair"""; WorkingDir: "{app}"; Description: "Pair this PC with the Hub now"; Flags: postinstall nowait skipifsilent; Check: not IsAlreadyPaired

[UninstallRun]
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File ""{app}\uninstall_task.ps1"""; RunOnceId: "RemoveScheduledTask"; Flags: runhidden waituntilterminated

[Code]
var
  SlicerPathsPage: TWizardPage;
  SlicerList: TNewCheckListBox;
  BtnModify, BtnRemove: TNewButton;
  StatusLbl: TNewStaticText;
  EntryKinds, EntryLabels, EntryPaths: array of string;
  EntryCustom: array of Boolean;
  SlicerPathsChanged: Boolean;

function IsAlreadyPaired: Boolean;
var
  Data: string;
begin
  Data := ExpandConstant('{localappdata}\filament-kit-sync');
  Result := FileExists(Data + '\token') and
            (FileExists(Data + '\config.xml') or FileExists(Data + '\config.json'));
end;

procedure UpdateSlicerButtons;
var
  HasSel: Boolean;
begin
  HasSel := (SlicerList.ItemIndex >= 0) and (SlicerList.ItemIndex < GetArrayLength(EntryPaths));
  if BtnModify <> nil then
    BtnModify.Enabled := HasSel;
  if BtnRemove <> nil then
    BtnRemove.Enabled := HasSel;
end;

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

procedure DetectOrcaFromUninstall(RootKey: Integer; BaseKey: string);
var
  Subkeys: TArrayOfString;
  I: Integer;
  SubPath, DisplayName, InstallDir: string;
begin
  if not RegGetSubkeyNames(RootKey, BaseKey, Subkeys) then
    exit;
  for I := 0 to GetArrayLength(Subkeys) - 1 do
  begin
    SubPath := BaseKey + '\' + Subkeys[I];
    if RegQueryStringValue(RootKey, SubPath, 'DisplayName', DisplayName) and
       (Pos('orcaslicer', Lowercase(Trim(DisplayName))) = 1) then
    begin
      if RegQueryStringValue(RootKey, SubPath, 'InstallLocation', InstallDir) then
      begin
        InstallDir := RemoveBackslashUnlessRoot(Trim(InstallDir));
        if (InstallDir <> '') and FileExists(InstallDir + '\orca-slicer.exe') then
          AddSlicerEntry('orca_install', 'Orca Slicer Install', InstallDir, True, False);
      end;
    end;
  end;
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

  DetectOrcaFromUninstall(HKEY_LOCAL_MACHINE, 'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall');
  DetectOrcaFromUninstall(HKEY_LOCAL_MACHINE, 'SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall');
  DetectOrcaFromUninstall(HKEY_CURRENT_USER, 'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall');
  DetectOrcaFromUninstall(HKEY_CURRENT_USER, 'SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall');

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

function ExtractXmlTag(S, Tag: string): string;
var
  P, Q: Integer;
  OpenTag, CloseTag: string;
begin
  Result := '';
  OpenTag := '<' + Tag + '>';
  CloseTag := '</' + Tag + '>';
  P := Pos(OpenTag, S);
  if P <= 0 then
    exit;
  Delete(S, 1, P + Length(OpenTag) - 1);
  Q := Pos(CloseTag, S);
  if Q > 0 then
    Result := Copy(S, 1, Q - 1);
end;

function LoadExistingSlicerPaths: Boolean;
var
  CfgFile, S, Block, KindVal, LabelVal, PathVal, EnabledVal, CustomVal: string;
  Raw: AnsiString;
  P, Q: Integer;
begin
  Result := False;
  CfgFile := ExpandConstant('{localappdata}\filament-kit-sync\config.xml');
  if (not FileExists(CfgFile)) or (not LoadStringFromFile(CfgFile, Raw)) then
    exit;
  S := ExtractXmlTag(String(Raw), 'slicer_paths');
  if S = '' then
    exit;
  while True do
  begin
    P := Pos('<item>', S);
    if P <= 0 then
      break;
    Delete(S, 1, P + Length('<item>') - 1);
    Q := Pos('</item>', S);
    if Q <= 0 then
      break;
    Block := Copy(S, 1, Q - 1);
    Delete(S, 1, Q + Length('</item>') - 1);
    KindVal := Trim(ExtractXmlTag(Block, 'kind'));
    LabelVal := Trim(ExtractXmlTag(Block, 'label'));
    PathVal := Trim(ExtractXmlTag(Block, 'path'));
    EnabledVal := Lowercase(Trim(ExtractXmlTag(Block, 'enabled')));
    CustomVal := Lowercase(Trim(ExtractXmlTag(Block, 'custom')));
    if (KindVal <> '') and (PathVal <> '') then
    begin
      if LabelVal = '' then
        LabelVal := ExtractFileName(PathVal);
      AddSlicerEntry(KindVal, LabelVal, PathVal, EnabledVal <> 'false', CustomVal = 'true');
      Result := True;
    end;
  end;
end;

procedure OnSlicerListClick(Sender: TObject);
begin
  UpdateSlicerButtons;
end;

procedure OnSlicerListClickCheck(Sender: TObject);
begin
  SlicerPathsChanged := True;
end;

procedure OnAutoDetectClick(Sender: TObject);
var
  BeforeCount, Added: Integer;
begin
  BeforeCount := GetArrayLength(EntryPaths);
  AutoDetectSlicerPaths;
  Added := GetArrayLength(EntryPaths) - BeforeCount;
  if Added > 0 then
  begin
    SlicerPathsChanged := True;
    StatusLbl.Caption := 'Found ' + IntToStr(Added) + ' new slicer folder(s).';
  end
  else
    StatusLbl.Caption := 'No new slicer folders found.';
  UpdateSlicerButtons;
end;

var
  DlgPathEdit: TNewEdit;

procedure OnDialogBrowseClick(Sender: TObject);
var
  Dir: string;
begin
  Dir := Trim(DlgPathEdit.Text);
  if Dir = '' then
    Dir := ExpandConstant('{userappdata}');
  if BrowseForFolder('Select Bambu Studio or Orca Slicer folder:', Dir, False) then
    DlgPathEdit.Text := RemoveBackslashUnlessRoot(Trim(Dir));
end;

function PromptSlicerEntry(Title: string; var Kind, LabelText, Dir: string): Boolean;
var
  Form: TSetupForm;
  KindCombo: TNewComboBox;
  LabelEdit: TNewEdit;
  BrowseBtn, OkBtn, CancelBtn: TNewButton;
  Lbl1, Lbl2, Lbl3: TNewStaticText;
  TopY: Integer;
begin
  Result := False;
  Form := CreateCustomForm(420, 170, True, True);
  try
    Form.Caption := Title;

    TopY := ScaleY(12);
    Lbl1 := TNewStaticText.Create(Form);
    Lbl1.Parent := Form;
    Lbl1.Left := ScaleX(12);
    Lbl1.Top := TopY + ScaleY(3);
    Lbl1.Caption := 'Kind:';

    KindCombo := TNewComboBox.Create(Form);
    KindCombo.Parent := Form;
    KindCombo.Left := ScaleX(76);
    KindCombo.Top := TopY;
    KindCombo.Width := ScaleX(200);
    KindCombo.Style := csDropDownList;
    KindCombo.Items.Add('Bambu Studio');
    KindCombo.Items.Add('Orca Slicer');
    KindCombo.Items.Add('Orca Install');
    if Kind = 'orca' then
      KindCombo.ItemIndex := 1
    else if Kind = 'orca_install' then
      KindCombo.ItemIndex := 2
    else
      KindCombo.ItemIndex := 0;

    TopY := TopY + ScaleY(30);
    Lbl2 := TNewStaticText.Create(Form);
    Lbl2.Parent := Form;
    Lbl2.Left := ScaleX(12);
    Lbl2.Top := TopY + ScaleY(3);
    Lbl2.Caption := 'Label:';

    LabelEdit := TNewEdit.Create(Form);
    LabelEdit.Parent := Form;
    LabelEdit.Left := ScaleX(76);
    LabelEdit.Top := TopY;
    LabelEdit.Width := ScaleX(332);
    LabelEdit.Text := LabelText;

    TopY := TopY + ScaleY(30);
    Lbl3 := TNewStaticText.Create(Form);
    Lbl3.Parent := Form;
    Lbl3.Left := ScaleX(12);
    Lbl3.Top := TopY + ScaleY(3);
    Lbl3.Caption := 'Path:';

    DlgPathEdit := TNewEdit.Create(Form);
    DlgPathEdit.Parent := Form;
    DlgPathEdit.Left := ScaleX(76);
    DlgPathEdit.Top := TopY;
    DlgPathEdit.Width := ScaleX(244);
    DlgPathEdit.Text := Dir;

    BrowseBtn := TNewButton.Create(Form);
    BrowseBtn.Parent := Form;
    BrowseBtn.Left := ScaleX(328);
    BrowseBtn.Top := TopY - ScaleY(1);
    BrowseBtn.Width := ScaleX(80);
    BrowseBtn.Height := ScaleY(23);
    BrowseBtn.Caption := 'Browse...';
    BrowseBtn.OnClick := @OnDialogBrowseClick;

    OkBtn := TNewButton.Create(Form);
    OkBtn.Parent := Form;
    OkBtn.Left := ScaleX(244);
    OkBtn.Top := ScaleY(130);
    OkBtn.Width := ScaleX(78);
    OkBtn.Height := ScaleY(24);
    OkBtn.Caption := 'OK';
    OkBtn.ModalResult := mrOk;
    OkBtn.Default := True;

    CancelBtn := TNewButton.Create(Form);
    CancelBtn.Parent := Form;
    CancelBtn.Left := ScaleX(330);
    CancelBtn.Top := ScaleY(130);
    CancelBtn.Width := ScaleX(78);
    CancelBtn.Height := ScaleY(24);
    CancelBtn.Caption := 'Cancel';
    CancelBtn.ModalResult := mrCancel;
    CancelBtn.Cancel := True;

    Form.FlipAndCenterIfNeeded(True, WizardForm, False);
    if Form.ShowModal() = mrOk then
    begin
      Dir := RemoveBackslashUnlessRoot(Trim(DlgPathEdit.Text));
      if Dir = '' then
        exit;
      if KindCombo.ItemIndex = 1 then
        Kind := 'orca'
      else if KindCombo.ItemIndex = 2 then
        Kind := 'orca_install'
      else
        Kind := 'bambu';
      LabelText := Trim(LabelEdit.Text);
      if LabelText = '' then
        LabelText := ExtractFileName(Dir);
      Result := True;
    end;
  finally
    DlgPathEdit := nil;
    Form.Free();
  end;
end;

procedure OnModifyClick(Sender: TObject);
var
  Idx: Integer;
  Kind, LabelText, Dir: string;
begin
  Idx := SlicerList.ItemIndex;
  if (Idx < 0) or (Idx >= GetArrayLength(EntryPaths)) then
    exit;
  Kind := EntryKinds[Idx];
  LabelText := EntryLabels[Idx];
  Dir := EntryPaths[Idx];
  if PromptSlicerEntry('Modify Slicer Folder', Kind, LabelText, Dir) then
  begin
    EntryKinds[Idx] := Kind;
    EntryLabels[Idx] := LabelText;
    EntryPaths[Idx] := Dir;
    EntryCustom[Idx] := True;
    SlicerList.ItemCaption[Idx] := LabelText + ' - ' + Dir;
    SlicerPathsChanged := True;
    StatusLbl.Caption := 'Updated slicer folder.';
  end;
end;

procedure OnAddClick(Sender: TObject);
var
  Dir, Kind, LabelText: string;
begin
  Dir := ExpandConstant('{userappdata}');
  Kind := 'bambu';
  LabelText := '';
  if PromptSlicerEntry('Add Slicer Folder', Kind, LabelText, Dir) then
  begin
    AddSlicerEntry(Kind, LabelText, Dir, True, True);
    SlicerPathsChanged := True;
    StatusLbl.Caption := 'Added slicer folder.';
    UpdateSlicerButtons;
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
  SlicerPathsChanged := True;
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
  StatusLbl.Caption := 'Removed slicer folder.';
  UpdateSlicerButtons;
end;

procedure InitializeWizard;
var
  BtnAutoDetect, BtnAdd: TNewButton;
  BtnTop, BtnWidth, BtnHeight, Gap: Integer;
begin
  SlicerPathsPage := CreateCustomPage(wpWelcome, 'Slicer Locations', 'Select, modify, or add the Bambu Studio and Orca Slicer folders to sync.');

  SlicerList := TNewCheckListBox.Create(SlicerPathsPage);
  SlicerList.Parent := SlicerPathsPage.Surface;
  SlicerList.Left := 0;
  SlicerList.Top := 0;
  SlicerList.Width := SlicerPathsPage.SurfaceWidth;
  SlicerList.Height := SlicerPathsPage.SurfaceHeight - ScaleY(56);
  SlicerList.Anchors := [akLeft, akTop, akRight, akBottom];
  SlicerList.OnClick := @OnSlicerListClick;
  SlicerList.OnClickCheck := @OnSlicerListClickCheck;

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

  StatusLbl := TNewStaticText.Create(SlicerPathsPage);
  StatusLbl.Parent := SlicerPathsPage.Surface;
  StatusLbl.Left := 0;
  StatusLbl.Top := BtnTop + BtnHeight + ScaleY(6);
  StatusLbl.Width := SlicerPathsPage.SurfaceWidth;
  StatusLbl.Caption := '';
  StatusLbl.Anchors := [akLeft, akRight, akBottom];

  SlicerPathsChanged := False;
  if not LoadExistingSlicerPaths then
    AutoDetectSlicerPaths;
  UpdateSlicerButtons;
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
    else if Ch = #9 then
      Result := Result + '\t'
    else if Ch = #10 then
      Result := Result + '\n'
    else if Ch = #13 then
      Result := Result + '\r'
    else
      Result := Result + Ch;
  end;
end;

procedure SaveSlicerPaths;
var
  I, N, ResultCode: Integer;
  Json, JsonFile, EnabledStr, CustomStr: string;
  Lines: TArrayOfString;
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
  SetArrayLength(Lines, 1);
  Lines[0] := Json;
  if SaveStringsToUTF8File(JsonFile, Lines, False) then
  begin
    ResultCode := -1;
    if not Exec(ExpandConstant('{app}\{#ConfigExeName}'), '--apply-paths "' + JsonFile + '"',
        '', SW_HIDE, ewWaitUntilTerminated, ResultCode) or (ResultCode <> 0) then
    begin
      Log('SaveSlicerPaths failed, exit code ' + IntToStr(ResultCode));
      SuppressibleMsgBox('Could not save slicer locations (exit code ' + IntToStr(ResultCode) +
        '). You can configure them later from Configure Slicer Sync.', mbError, MB_OK, IDOK);
    end;
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
var
  Data: string;
  HasConfig: Boolean;
begin
  if CurStep = ssPostInstall then
  begin
    Data := ExpandConstant('{localappdata}\filament-kit-sync');
    HasConfig := FileExists(Data + '\config.xml') or FileExists(Data + '\config.json');
    if SlicerPathsChanged or ((not WizardSilent) and (not HasConfig)) then
      SaveSlicerPaths;
    RegisterTask;
  end;
end;

// Reads the paired device id from config.xml (or legacy config.json) before data deletion.
function ReadDeviceId(Data: string): string;
var
  Raw: AnsiString;
  S, Tag: string;
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
    S := String(Raw);
    P := Pos('<config', S);
    if P > 0 then
    begin
      Delete(S, 1, P + Length('<config') - 1);
      Q := Pos('>', S);
      if Q > 0 then
      begin
        Tag := Copy(S, 1, Q - 1);
        P := Pos('device="', Tag);
        while (P > 1) and (Tag[P - 1] > ' ') do
        begin
          Delete(Tag, 1, P + Length('device="') - 1);
          P := Pos('device="', Tag);
        end;
        if P > 0 then
        begin
          Delete(Tag, 1, P + Length('device="') - 1);
          Q := Pos('"', Tag);
          if Q > 0 then
          begin
            Result := Trim(Copy(Tag, 1, Q - 1));
            if Result <> '' then
              exit;
          end;
        end;
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
