// Included in [Code]. Budget definitions are measured from the staged release.
const
  PayloadBytes = '{#PayloadBytes}';
  InstallBudgetBytes = '{#InstallBudgetBytes}';
  TemporaryBudgetBytes = '{#TemporaryBudgetBytes}';
  DataHeadroomBytes = '{#DataHeadroomBytes}';

type
  TSpaceRole = record
    Name, Path, Volume: String;
    Needed, Free: Int64;
    Known: Boolean;
  end;
  TSpaceRoles = array[0..2] of TSpaceRole;

var
  SpacePage: TWizardPage;
  SpaceDetails: TNewMemo;
  SpaceStatus, LocationText: TNewStaticText;
  ChangeLocation, RefreshSpace, UseRecommended: TNewButton;
  DesktopChoice, StartupChoice: TNewCheckBox;
  RecommendedPath: String;
  ChoicesLoaded: Boolean;

function VolumePathName(Path, Buffer: String; Size: Cardinal): Boolean;
  external 'GetVolumePathNameW@kernel32.dll stdcall';
function VolumeName(Path, Buffer: String; Size: Cardinal): Boolean;
  external 'GetVolumeNameForVolumeMountPointW@kernel32.dll stdcall';
function DriveType(Path: String): Cardinal;
  external 'GetDriveTypeW@kernel32.dll stdcall';

function ExistingParent(Path: String): String;
var Parent: String;
begin
  Result := ExpandFileName(Path);
  while not DirExists(Result) do begin
    Parent := ExtractFileDir(RemoveBackslashUnlessRoot(Result));
    if (Parent = '') or (Parent = Result) then begin Result := ''; Exit; end;
    Result := Parent;
  end;
end;

function IdentifyVolume(Path: String; var Identity, Existing: String): Boolean;
var Mount, Name: String;
begin
  Result := False;
  Existing := ExistingParent(Path);
  if Existing = '' then Exit;
  Mount := StringOfChar(#0, 32768);
  if not VolumePathName(Existing, Mount, 32768) then Exit;
  Mount := Copy(Mount, 1, Pos(#0, Mount) - 1);
  // Local fixed and removable volumes only. Volume GUIDs include mounted folders
  // and drive aliases, so two paths to one disk never receive separate budgets.
  if (DriveType(Mount) <> 3) and (DriveType(Mount) <> 2) then Exit;
  Name := StringOfChar(#0, 32768);
  if not VolumeName(Mount, Name, 32768) then Exit;
  Identity := Lowercase(Copy(Name, 1, Pos(#0, Name) - 1));
  Result := Identity <> '';
end;

function Amount(Bytes: Int64): String;
begin
  Result := IntToStr((Bytes + 1048575) div 1048576) + ' MiB';
end;

function Assess(var Roles: TSpaceRoles; var Details, Problem: String): Boolean;
var I, J: Integer; Required, Available: Int64; Seen: Boolean;
begin
  Result := True; Details := ''; Problem := '';
  for I := 0 to 2 do begin
    Details := Details + Roles[I].Name + ': ' + Roles[I].Path + #13#10 +
      '  Additional space budget: ' + Amount(Roles[I].Needed) + #13#10;
    if not Roles[I].Known then begin
      Result := False;
      Problem := Problem + 'Cannot verify ' + Roles[I].Name + ' space. Choose an accessible local location and check again.' + #13#10;
    end;
  end;
  for I := 0 to 2 do begin
    Seen := False;
    for J := 0 to I - 1 do
      if Roles[J].Known and (Roles[J].Volume = Roles[I].Volume) then Seen := True;
    if Roles[I].Known and not Seen then begin
      Required := 0; Available := Roles[I].Free;
      for J := I to 2 do
        if Roles[J].Known and (Roles[J].Volume = Roles[I].Volume) then begin
          Required := Required + Roles[J].Needed;
          if Roles[J].Free < Available then Available := Roles[J].Free;
        end;
      Details := Details + #13#10 + 'Drive containing ' + Roles[I].Path + #13#10 +
        '  Available: ' + Amount(Available) + '   Combined budget: ' + Amount(Required) + #13#10;
      if Available < Required then begin
        Result := False;
        Problem := Problem + 'Free at least ' + Amount(Required - Available) +
          ' more on the drive containing ' + Roles[I].Path + '.' + #13#10;
      end;
    end;
  end;
end;

procedure ReadRole(var Role: TSpaceRole; Name, Path: String; Needed: Int64);
var Existing: String; Total: Int64;
begin
  Role.Name := Name; Role.Path := Path; Role.Needed := Needed;
  Role.Free := 0; Role.Volume := '';
  Role.Known := IdentifyVolume(Path, Role.Volume, Existing);
  if Role.Known then Role.Known := GetSpaceOnDisk64(Existing, Role.Free, Total);
#ifdef PREFLIGHT_TEST_BUILD
  if ExpandConstant('{param:SPACEFIXTURE|}') = 'low' then Role.Free := 0;
  if ExpandConstant('{param:SPACEFIXTURE|}') = 'unknown' then Role.Known := False;
  if (ExpandConstant('{param:SPACEFIXTURE|}') = 'changed') and
     FileExists(ExpandConstant('{param:SPACECHANGE|}')) then Role.Free := 0;
#endif
end;

function DataLocation: String;
begin
  Result := Trim(GetEnv('HOMESERVER_DATA_DIR'));
  if Result = '' then Result := ExpandConstant('{localappdata}') + '\HomeServer\Data';
end;

function CheckSpace(Target: String; var Details, Problem: String): Boolean;
var Roles: TSpaceRoles;
begin
  ReadRole(Roles[0], 'Application location', Target, StrToInt64(InstallBudgetBytes));
  ReadRole(Roles[1], 'Temporary setup and startup', ExpandConstant('{tmp}'), StrToInt64(TemporaryBudgetBytes));
  ReadRole(Roles[2], 'Private data headroom', DataLocation, StrToInt64(DataHeadroomBytes));
  Result := Assess(Roles, Details, Problem);
  Details := 'Application and included runtimes: ' + Amount(StrToInt64(PayloadBytes)) + #13#10 +
    'Budgets include temporary staging, upgrade and startup headroom.' + #13#10#13#10 + Details;
  if FileExists(AddBackslash(Target) + 'HomeServer.exe') then
    Details := Details + #13#10 + 'Existing HomeServer found at this location. Application files will be upgraded.';
  if FileExists(AddBackslash(Target) + 'tools\browser\manifest.json') then
    Details := Details + #13#10 + 'Managed browser detected.';
  if FileExists(AddBackslash(Target) + 'tools\ffmpeg\manifest.json') then
    Details := Details + #13#10 + 'Media runtime detected.';
  Details := Details + #13#10#13#10 + 'Your settings and private data are kept. Changing the application location does not move private data or Windows temporary storage.' + #13#10 +
    'Allow additional space for future media, models, backups and data migrations.';
end;

procedure RefreshCheck(Sender: TObject);
var Details, Problem, Candidate, IgnoreDetails, IgnoreProblem: String;
    Ready: Boolean; Letter: Integer;
begin
  Ready := CheckSpace(WizardDirValue, Details, Problem);
  LocationText.Caption := WizardDirValue;
  SpaceDetails.Text := Details;
  RecommendedPath := '';
  if Ready then begin
    SpaceStatus.Caption := 'Ready to install'; SpaceStatus.Font.Color := clGreen;
  end else begin
    SpaceStatus.Caption := 'More space or another location is needed'; SpaceStatus.Font.Color := clRed;
    SpaceDetails.Text := Problem + #13#10 + Details;
    for Letter := 65 to 90 do begin
      Candidate := Chr(Letter) + ':\';
      if DriveType(Candidate) = 3 then begin
        Candidate := Candidate + 'HomeServer';
        if CheckSpace(Candidate, IgnoreDetails, IgnoreProblem) then begin
          RecommendedPath := Candidate; Break;
        end;
      end;
    end;
  end;
  UseRecommended.Visible := RecommendedPath <> '';
  if RecommendedPath <> '' then UseRecommended.Caption := 'Use ' + RecommendedPath;
  if WizardForm.CurPageID = SpacePage.ID then WizardForm.NextButton.Enabled := Ready;
end;

procedure ChooseLocation(Sender: TObject);
var Path: String;
begin
  Path := WizardDirValue;
  if BrowseForFolder('Choose the HomeServer application folder', Path, True) then begin
    if Length(RemoveBackslashUnlessRoot(Path)) <= 3 then Path := AddBackslash(Path) + 'HomeServer';
    WizardForm.DirEdit.Text := Path;
    RefreshCheck(Sender);
  end;
end;

procedure SelectRecommended(Sender: TObject);
begin
  if RecommendedPath <> '' then begin
    WizardForm.DirEdit.Text := RecommendedPath;
    RefreshCheck(Sender);
  end;
end;

procedure InitializeWizard;
begin
  SpacePage := CreateCustomPage(wpWelcome, 'Check your computer', 'Review available space, then install HomeServer.');
  SpaceStatus := TNewStaticText.Create(SpacePage);
  SpaceStatus.Parent := SpacePage.Surface; SpaceStatus.SetBounds(0, 0, SpacePage.SurfaceWidth, ScaleY(22));
  SpaceStatus.Font.Style := [fsBold];
  LocationText := TNewStaticText.Create(SpacePage);
  LocationText.Parent := SpacePage.Surface; LocationText.AutoSize := False;
  LocationText.SetBounds(0, ScaleY(26), SpacePage.SurfaceWidth - ScaleX(118), ScaleY(32));
  LocationText.WordWrap := True;
  ChangeLocation := TNewButton.Create(SpacePage); ChangeLocation.Parent := SpacePage.Surface;
  ChangeLocation.SetBounds(SpacePage.SurfaceWidth - ScaleX(110), ScaleY(26), ScaleX(110), ScaleY(25));
  ChangeLocation.Caption := 'Change location...'; ChangeLocation.OnClick := @ChooseLocation;
  SpaceDetails := TNewMemo.Create(SpacePage); SpaceDetails.Parent := SpacePage.Surface;
  SpaceDetails.SetBounds(0, ScaleY(64), SpacePage.SurfaceWidth, SpacePage.SurfaceHeight - ScaleY(155));
  SpaceDetails.ReadOnly := True; SpaceDetails.ScrollBars := ssVertical; SpaceDetails.WordWrap := True;
  RefreshSpace := TNewButton.Create(SpacePage); RefreshSpace.Parent := SpacePage.Surface;
  RefreshSpace.SetBounds(0, SpacePage.SurfaceHeight - ScaleY(83), ScaleX(105), ScaleY(25));
  RefreshSpace.Caption := 'Check again'; RefreshSpace.OnClick := @RefreshCheck;
  UseRecommended := TNewButton.Create(SpacePage); UseRecommended.Parent := SpacePage.Surface;
  UseRecommended.SetBounds(ScaleX(115), SpacePage.SurfaceHeight - ScaleY(83), ScaleX(230), ScaleY(25));
  UseRecommended.OnClick := @SelectRecommended;
  DesktopChoice := TNewCheckBox.Create(SpacePage); DesktopChoice.Parent := SpacePage.Surface;
  DesktopChoice.SetBounds(0, SpacePage.SurfaceHeight - ScaleY(50), SpacePage.SurfaceWidth, ScaleY(21));
  DesktopChoice.Caption := 'Create a desktop shortcut';
  StartupChoice := TNewCheckBox.Create(SpacePage); StartupChoice.Parent := SpacePage.Surface;
  StartupChoice.SetBounds(0, SpacePage.SurfaceHeight - ScaleY(25), SpacePage.SurfaceWidth, ScaleY(21));
  StartupChoice.Caption := 'Start HomeServer when I sign in to Windows';
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = SpacePage.ID then begin
    if not ChoicesLoaded then begin
      DesktopChoice.Checked := WizardIsTaskSelected('desktopicon');
      StartupChoice.Checked := WizardIsTaskSelected('startup');
      ChoicesLoaded := True;
    end;
    WizardForm.NextButton.Caption := '&Install';
    RefreshCheck(nil);
  end;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := (PageID = wpSelectTasks) or (PageID = wpSelectProgramGroup);
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var Details, Problem: String;
begin
  Result := True;
  if CurPageID = SpacePage.ID then begin
    Result := CheckSpace(WizardDirValue, Details, Problem);
    if not Result then begin
      Log('HomeServer space check blocked: ' + Problem);
      if not WizardSilent then RefreshCheck(nil);
    end else if not WizardSilent then begin
      WizardSelectTasks('!desktopicon,!startup');
      if DesktopChoice.Checked then WizardSelectTasks('desktopicon');
      if StartupChoice.Checked then WizardSelectTasks('startup');
    end;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var Details, Problem: String;
begin
  Result := '';
  // Re-read free space here, including silent upgrades; a prior UI pass is not authority.
  if not CheckSpace(WizardDirValue, Details, Problem) then Result := Problem;
  Log('HomeServer pre-install space check: ' + Details);
end;

#ifdef PREFLIGHT_TEST_BUILD
  #include "SpaceCheckTests.iss"
#endif
