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
Name: "{group}\Social Auto Upload Command Line"; Filename: "{app}\sau.exe"; WorkingDir: "{app}"

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

function RemovePathEntry(const PathValue, Entry: String): String;
var
  Remaining: String;
  Segment: String;
  Separator: Integer;
begin
  Result := '';
  Remaining := PathValue + ';';
  while Length(Remaining) > 0 do
  begin
    Separator := Pos(';', Remaining);
    Segment := Trim(Copy(Remaining, 1, Separator - 1));
    Delete(Remaining, 1, Separator);
    if (Segment <> '') and
       (CompareText(NormalizePathEntry(Segment), NormalizePathEntry(Entry)) <> 0) then
    begin
      if Result <> '' then
        Result := Result + ';';
      Result := Result + Segment;
    end;
  end;
end;

procedure AddUserPath;
var
  ExistingPath: String;
  AppPath: String;
begin
  AppPath := ExpandConstant('{app}');
  if not RegQueryStringValue(HKCU, UserEnvironmentKey, 'Path', ExistingPath) then
    ExistingPath := '';
  if PathContains(ExistingPath, AppPath) then
    Exit;
  if ExistingPath = '' then
    ExistingPath := AppPath
  else
    ExistingPath := ExistingPath + ';' + AppPath;
  if not RegWriteExpandStringValue(HKCU, UserEnvironmentKey, 'Path', ExistingPath) then
    RaiseException('Unable to update the user PATH.');
  RegWriteDWordValue(HKCU, InstallerStateKey, 'AddedToPath', 1);
end;

procedure RemoveUserPath;
var
  Added: Cardinal;
  ExistingPath: String;
begin
  if not RegQueryDWordValue(HKCU, InstallerStateKey, 'AddedToPath', Added) or
     (Added <> 1) then
    Exit;
  if RegQueryStringValue(HKCU, UserEnvironmentKey, 'Path', ExistingPath) then
    RegWriteExpandStringValue(
      HKCU, UserEnvironmentKey, 'Path',
      RemovePathEntry(ExistingPath, ExpandConstant('{app}'))
    );
  RegDeleteValue(HKCU, InstallerStateKey, 'AddedToPath');
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
