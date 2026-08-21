#ifndef PayloadDir
  #error PayloadDir must point to a verified Windows x64 payload
#endif

#ifndef OutputDir
  #define OutputDir "."
#endif

#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

[Setup]
AppId={{5B1C95A7-3EC8-4AE1-AE64-D1CC1E2AD025}
AppName=Social Auto Upload
AppVersion={#AppVersion}
AppPublisher=Social Auto Upload
DefaultDirName={localappdata}\Programs\SocialAutoUpload
DefaultGroupName=Social Auto Upload
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutputDir}
OutputBaseFilename=SocialAutoUpload-Windows-x64-Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
Uninstallable=yes
UninstallDisplayName=Social Auto Upload
UninstallDisplayIcon={app}\SocialAutoUpload.exe
ChangesEnvironment=yes
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"
Name: "addtopath"; Description: "Add the command-line tool to my user PATH"; GroupDescription: "Command line:"; Flags: unchecked

[Files]
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autodesktop}\Social Auto Upload"; Filename: "{app}\SocialAutoUpload.exe"; WorkingDir: "{app}"; Tasks: desktopicon
Name: "{group}\Social Auto Upload"; Filename: "{app}\SocialAutoUpload.exe"; WorkingDir: "{app}"
Name: "{group}\Social Auto Upload Command Line"; Filename: "{cmd}"; Parameters: "/K """"{app}\sau.exe"" --help"""; WorkingDir: "{app}"

[Run]
Filename: "{app}\SocialAutoUpload.exe"; Description: "Launch Social Auto Upload"; Flags: nowait postinstall skipifsilent

[Code]
const
  UserEnvironmentKey = 'Environment';
  InstallerStateKey = 'Software\SocialAutoUpload\Installer';

function NormalizePathEntry(const Value: String): String;
begin
  Result := Trim(Value);
  if (Length(Result) >= 2) and (Result[1] = '"') and
     (Result[Length(Result)] = '"') then
  begin
    Result := Copy(Result, 2, Length(Result) - 2);
  end;
  while (Length(Result) > 3) and (Result[Length(Result)] = '\') do
  begin
    Delete(Result, Length(Result), 1);
  end;
end;

function PathContains(const PathValue, Entry: String): Boolean;
var
  Remaining: String;
  Segment: String;
  Separator: Integer;
begin
  Result := False;
  Remaining := PathValue + ';';
  while Length(Remaining) > 0 do
  begin
    Separator := Pos(';', Remaining);
    Segment := Copy(Remaining, 1, Separator - 1);
    Delete(Remaining, 1, Separator);
    if CompareText(NormalizePathEntry(Segment), NormalizePathEntry(Entry)) = 0 then
    begin
      Result := True;
      Exit;
    end;
  end;
end;

function FindLastOwnedSegment(const PathValue, Entry: String): Integer;
var
  Remaining: String;
  Segment: String;
  Separator: Integer;
  HasMore: Boolean;
  SegmentIndex: Integer;
begin
  Result := -1;
  Remaining := PathValue;
  SegmentIndex := 0;
  while True do
  begin
    Separator := Pos(';', Remaining);
    HasMore := Separator > 0;
    if HasMore then
    begin
      Segment := Copy(Remaining, 1, Separator - 1);
      Delete(Remaining, 1, Separator);
    end
    else
    begin
      Segment := Remaining;
      Remaining := '';
    end;
    if CompareText(Segment, Entry) = 0 then
      Result := SegmentIndex;
    SegmentIndex := SegmentIndex + 1;
    if not HasMore then
      Break;
  end;
end;

function RemovePathEntry(const PathValue, Entry: String): String;
var
  Remaining: String;
  Segment: String;
  Separator: Integer;
  HasMore: Boolean;
  FirstSegment: Boolean;
  SegmentIndex: Integer;
  LastOwnedSegment: Integer;
begin
  LastOwnedSegment := FindLastOwnedSegment(PathValue, Entry);
  if LastOwnedSegment < 0 then
  begin
    Result := PathValue;
    Exit;
  end;
  Result := '';
  Remaining := PathValue;
  FirstSegment := True;
  SegmentIndex := 0;
  while True do
  begin
    Separator := Pos(';', Remaining);
    HasMore := Separator > 0;
    if HasMore then
    begin
      Segment := Copy(Remaining, 1, Separator - 1);
      Delete(Remaining, 1, Separator);
    end
    else
    begin
      Segment := Remaining;
      Remaining := '';
    end;
    if SegmentIndex = LastOwnedSegment then
    begin
    end
    else
    begin
      if not FirstSegment then
        Result := Result + ';';
      Result := Result + Segment;
      FirstSegment := False;
    end;
    SegmentIndex := SegmentIndex + 1;
    if not HasMore then
      Break;
  end;
end;

function RollbackUserPath(const PathExisted: Boolean; const PreviousPath: String): Boolean;
begin
  if PathExisted then
    Result := RegWriteExpandStringValue(HKCU, UserEnvironmentKey, 'Path', PreviousPath)
  else
    Result := RegDeleteValue(HKCU, UserEnvironmentKey, 'Path');
end;

procedure AddUserPath;
var
  PreviousPath: String;
  PreviousOwnedPath: String;
  UpdatedPath: String;
  AppPath: String;
  PathExisted: Boolean;
  PreviousOwnershipExisted: Boolean;
begin
  AppPath := ExpandConstant('{app}');
  PathExisted := RegQueryStringValue(HKCU, UserEnvironmentKey, 'Path', PreviousPath);
  if not PathExisted then
    PreviousPath := '';
  PreviousOwnershipExisted := RegQueryStringValue(
    HKCU, InstallerStateKey, 'OwnedPath', PreviousOwnedPath
  );
  if PreviousOwnershipExisted and
     (CompareText(NormalizePathEntry(PreviousOwnedPath), NormalizePathEntry(AppPath)) = 0) and
     PathContains(PreviousPath, AppPath) then
    Exit;

  UpdatedPath := PreviousPath;
  if PreviousOwnershipExisted then
    UpdatedPath := RemovePathEntry(PreviousPath, PreviousOwnedPath);

  if PathContains(UpdatedPath, AppPath) then
  begin
    if UpdatedPath <> PreviousPath then
    begin
      if not RegWriteExpandStringValue(HKCU, UserEnvironmentKey, 'Path', UpdatedPath) then
        RaiseException('Unable to remove the prior installer-owned PATH entry.');
    end;
    if PreviousOwnershipExisted then
    begin
      if not RegDeleteValue(HKCU, InstallerStateKey, 'OwnedPath') then
      begin
        if not RollbackUserPath(PathExisted, PreviousPath) then
          RaiseException('Unable to clear old PATH ownership and PATH rollback failed.');
        RaiseException('Unable to clear old PATH ownership; the PATH update was rolled back.');
      end;
    end;
    Exit;
  end;

  if UpdatedPath = '' then
    UpdatedPath := AppPath
  else
    UpdatedPath := UpdatedPath + ';' + AppPath;
  if not RegWriteExpandStringValue(HKCU, UserEnvironmentKey, 'Path', UpdatedPath) then
    RaiseException('Unable to update the user PATH.');
  if not RegWriteStringValue(HKCU, InstallerStateKey, 'OwnedPath', AppPath) then
  begin
    if not RollbackUserPath(PathExisted, PreviousPath) then
      RaiseException('Unable to record PATH ownership and PATH rollback failed.');
    RaiseException('Unable to record PATH ownership; the PATH update was rolled back.');
  end;
end;

procedure RemoveUserPath;
var
  OwnedPath: String;
  ExistingPath: String;
  UpdatedPath: String;
begin
  if not RegQueryStringValue(HKCU, InstallerStateKey, 'OwnedPath', OwnedPath) then
    Exit;
  if RegQueryStringValue(HKCU, UserEnvironmentKey, 'Path', ExistingPath) then
  begin
    UpdatedPath := RemovePathEntry(ExistingPath, OwnedPath);
    if UpdatedPath <> ExistingPath then
    begin
      if not RegWriteExpandStringValue(HKCU, UserEnvironmentKey, 'Path', UpdatedPath) then
        RaiseException('Unable to remove the installer-owned user PATH entry.');
    end;
  end;
  if not RegDeleteValue(HKCU, InstallerStateKey, 'OwnedPath') then
    RaiseException('Unable to delete the installer PATH ownership marker.');
  RegDeleteKeyIfEmpty(HKCU, InstallerStateKey);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and WizardIsTaskSelected('addtopath') then
    AddUserPath;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    RemoveUserPath;
end;
