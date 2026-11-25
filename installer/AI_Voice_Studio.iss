#define MyAppName "AI Voice Studio"
#define MyAppVersion "0.22.0"
#define MyBuiltExe "AI_Voice_Studio.exe"
#define MyAppExeName "Voice Studio V0.1.exe"
#define MyShortcutName "Voice Studio V0.1"

[Setup]
AppId={{AC43A22B-744E-4969-8CE0-4D9C4A4BDF5A}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName=AI Voice Studio by Unzip Spark
AppPublisher=Unzip Spark
DefaultDirName={localappdata}\Programs\AI Voice Studio
DefaultGroupName=AI Voice Studio
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
DisableDirPage=auto
UsePreviousAppDir=yes
AlwaysShowDirOnReadyPage=yes
CloseApplications=yes
RestartApplications=no
Compression=lzma2
SolidCompression=yes
OutputDir=..\BuildOut
OutputBaseFilename=AI_Voice_Studio_Setup_0.22.0
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName=AI Voice Studio by Unzip Spark
SetupLogging=yes
SetupIconFile=..\resources\avs.ico
UninstallDisplayIcon={app}\{#MyAppExeName}

[Files]
Source: "..\dist\AI_Voice_Studio\*"; DestDir: "{app}"; Excludes: "{#MyBuiltExe}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\dist\AI_Voice_Studio\{#MyBuiltExe}"; DestDir: "{app}"; DestName: "{#MyAppExeName}"; Flags: ignoreversion

[InstallDelete]
; Older versions used other names for the program file and shortcuts.
Type: files; Name: "{app}\{#MyBuiltExe}"
Type: files; Name: "{autoprograms}\AI Voice Studio.lnk"
Type: files; Name: "{autodesktop}\AI Voice Studio.lnk"

[Icons]
Name: "{autoprograms}\{#MyShortcutName}"; Filename: "{app}\{#MyAppExeName}"; AppUserModelID: "AI.Voice.Studio.Desktop"
Name: "{autodesktop}\{#MyShortcutName}"; Filename: "{app}\{#MyAppExeName}"; AppUserModelID: "AI.Voice.Studio.Desktop"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch Voice Studio"; WorkingDir: "{app}"; Flags: shellexec nowait postinstall skipifsilent runasoriginaluser

[UninstallRun]
Filename: "schtasks.exe"; Parameters: "/delete /tn ""AI Voice Studio (admin start)"" /f"; Flags: runhidden; RunOnceId: "DeleteAdminStartTask"

[UninstallDelete]
; WARNING: Never remove %LOCALAPPDATA%\AVS or any user audio / model files.
; Inno removes only its own installed program files.

[Code]
var
  WasInstalled: Boolean;
procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
  OldStatus: String;
  RuntimeArgs: String;
begin
  if CurStep = ssInstall then
    WasInstalled := FileExists(ExpandConstant('{app}\{#MyAppExeName}')) or FileExists(ExpandConstant('{app}\{#MyBuiltExe}'));
  if CurStep = ssPostInstall then
  begin
    OldStatus := WizardForm.StatusLabel.Caption;
    WizardForm.StatusLabel.Caption := 'Checking application files and Python runtime (installing only if missing)...';
    Log('Starting installed-application prerequisite check.');
    RuntimeArgs := '--prepare-runtime';
    if WasInstalled or (ExpandConstant('{param:AVSOFFLINE|0}') = '1') then
    begin
      RuntimeArgs := '--prepare-runtime-offline';
      WizardForm.StatusLabel.Caption := 'Checking existing runtime and program files (offline update)...';
    end;
    if not Exec(ExpandConstant('{app}\{#MyAppExeName}'), RuntimeArgs, ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ResultCode) then
    begin
      Log('Could not start prerequisite check: ' + SysErrorMessage(ResultCode));
      if not WizardSilent then
        MsgBox('The program files were installed, but the prerequisite check could not start. Open AI Setup to repair Python.', mbError, MB_OK);
    end
    else if ResultCode <> 0 then
    begin
      Log('Prerequisite check reported exit code ' + IntToStr(ResultCode));
      if not WizardSilent then
        MsgBox('The program files were installed, but Python or a required file could not be verified. See %LOCALAPPDATA%\AVS\logs\setup-preflight.log and use AI Setup to repair Python. Internet and WinGet may be needed.', mbError, MB_OK);
    end
    else Log('Installed application prerequisite check passed.');
    WizardForm.StatusLabel.Caption := OldStatus;
  end;
end;
