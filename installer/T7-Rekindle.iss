#define MyAppName "T7-Rekindle"
#ifndef MyAppVersion
#define MyAppVersion "0.1.0"
#endif
#define MyAppPublisher "T7-Rekindle contributors"
#define MyAppExeName "T7-Rekindle.exe"

[Setup]
AppId={{A8D06D9F-5E67-4E77-9B6B-0B4A3C1A4F30}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppMutex=Local\T7-Rekindle.Desktop
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\T7-Rekindle
DisableDirPage=no
ArchitecturesInstallIn64BitMode=x64os
ArchitecturesAllowed=x64os
OutputDir=..\dist
OutputBaseFilename=T7-Rekindle-Setup
Compression=lzma
SolidCompression=yes
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
MinVersion=10.0.19041

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Files]
Source: "..\artifacts\package\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Tasks]
Name: "desktopicon"; Description: "添加桌面快捷方式"; GroupDescription: "附加选项:"; Flags: unchecked

[Icons]
Name: "{autoprograms}\T7-Rekindle"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\T7-Rekindle"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "运行 T7-Rekindle"; Flags: postinstall nowait; Check: AllowLauncherRun
Filename: "{app}"; Description: "打开安装目录"; Flags: postinstall shellexec skipifsilent unchecked

[Code]
#define RollbackSettingsSchemaVersion 1
#ifndef RollbackDataDirectory
#define RollbackDataDirectory "{localappdata}\T7-Rekindle"
#endif
#ifndef RollbackRunKey
#define RollbackRunKey "Software\Microsoft\Windows\CurrentVersion\Run"
#endif
const
  SYNCHRONIZE = $00100000;
  WAIT_OBJECT_0 = 0;
  WAIT_TIMEOUT = 258;
  ERROR_INVALID_PARAMETER = 87;

var
  RollbackActive, ResetSettings, RollbackReady, RollbackFailed, BackupComplete: Boolean;
  DataDirectory, BackupDirectory, UpdateChannel, StartupValue: String;
  StartupExisted: Boolean;
  SettingsNames: array[0..3] of String;
  SettingsExisted: array[0..3] of Boolean;

function AllowLauncherRun(): Boolean;
begin
  Result := not RollbackActive or RollbackReady;
end;

procedure RequireSuccess(Success: Boolean; MessageText: String);
begin
  if not Success then RaiseException(MessageText + '：' + SysErrorMessage(DLLGetLastError()));
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Root, MetadataPath, Source: String;
  I: Integer;
begin
  Result := '';
  if not RollbackActive or BackupComplete then Exit;
  try
    Root := DataDirectory + '\settings-backups';
    RequireSuccess(ForceDirectories(Root), '创建设置备份目录失败');
    BackupDirectory := GenerateUniqueName(Root, '.backup');
    RequireSuccess(BackupDirectory <> '', '分配设置备份目录失败');
    RequireSuccess(CreateDir(BackupDirectory), '创建独立备份目录失败');
    MetadataPath := BackupDirectory + '\backup.ini';
    for I := 0 to 3 do begin
      Source := DataDirectory + '\' + SettingsNames[I];
      RequireSuccess(not DirExists(Source), '设置路径不是文件');
      SettingsExisted[I] := FileExists(Source);
      if SettingsExisted[I] then
        RequireSuccess(FileCopy(Source, BackupDirectory + '\' + SettingsNames[I], True), '备份设置失败');
      RequireSuccess(SetIniBool('files', SettingsNames[I], SettingsExisted[I], MetadataPath), '记录设置备份失败');
    end;
    StartupExisted := RegValueExists(HKCU, '{#RollbackRunKey}', 'T7-Rekindle');
    if StartupExisted then
      RequireSuccess(RegQueryStringValue(HKCU, '{#RollbackRunKey}', 'T7-Rekindle', StartupValue), '读取登录启动项失败');
    RequireSuccess(SetIniBool('startup', 'existed', StartupExisted, MetadataPath), '记录登录启动项失败');
    RequireSuccess(SetIniString('startup', 'value', StartupValue, MetadataPath), '备份登录启动项失败');
    RequireSuccess(SetIniString('installation', 'directory', ExpandConstant('{app}'), MetadataPath), '记录安装目录失败');
    RequireSuccess(SetIniString('installation', 'channel', UpdateChannel, MetadataPath), '记录更新渠道失败');
    RequireSuccess(SetIniBool('installation', 'complete', True, MetadataPath), '完成设置备份失败');
    BackupComplete := True;
    Log('Rollback settings backup: ' + BackupDirectory);
  except
    Result := GetExceptionMessage();
  end;
end;

function RestoreSettings(): Boolean;
var
  I: Integer;
  Destination: String;
begin
  Result := True;
  for I := 0 to 3 do begin
    Destination := DataDirectory + '\' + SettingsNames[I];
    if SettingsExisted[I] then begin
      if not FileCopy(BackupDirectory + '\' + SettingsNames[I], Destination, False) then Result := False;
    end
    else if FileExists(Destination) then begin
      if not DeleteFile(Destination) then Result := False;
    end;
  end;
  if StartupExisted then begin
    if not RegWriteStringValue(HKCU, '{#RollbackRunKey}', 'T7-Rekindle', StartupValue) then Result := False;
  end
  else if RegValueExists(HKCU, '{#RollbackRunKey}', 'T7-Rekindle') then begin
    if not RegDeleteValue(HKCU, '{#RollbackRunKey}', 'T7-Rekindle') then Result := False;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  I: Integer;
  Path, Temporary, ErrorText: String;
begin
  if (CurStep <> ssPostInstall) or not RollbackActive then Exit;
  try
    RequireSuccess(BackupComplete, '设置备份尚未完成');
    if ResetSettings then begin
      Temporary := GenerateUniqueName(DataDirectory, '.rollback.tmp');
      RequireSuccess(not FileExists(Temporary) and not DirExists(Temporary), '设置重置临时路径已存在');
      RequireSuccess(SaveStringToFile(Temporary, '{"schemaVersion": {#RollbackSettingsSchemaVersion}, "updateChannel": "' + UpdateChannel + '"}' + #13#10, False), '写入目标版本设置失败');
      for I := 0 to 3 do begin
        Path := DataDirectory + '\' + SettingsNames[I];
        if FileExists(Path) then RequireSuccess(DeleteFile(Path), '重置设置失败');
      end;
      RequireSuccess(RenameFile(Temporary, DataDirectory + '\settings.json'), '保存重置设置失败');
      if RegValueExists(HKCU, '{#RollbackRunKey}', 'T7-Rekindle') then
        RequireSuccess(RegDeleteValue(HKCU, '{#RollbackRunKey}', 'T7-Rekindle'), '重置登录启动项失败');
    end;
    RollbackReady := True;
  except
    RollbackFailed := True;
    ErrorText := GetExceptionMessage();
    if (Temporary <> '') and FileExists(Temporary) then
      if not DeleteFile(Temporary) then ErrorText := ErrorText + #13#10 + '清理重置临时文件失败。';
    if not RestoreSettings() then ErrorText := ErrorText + #13#10 + '恢复原设置时发生错误，请从备份恢复。';
    ErrorText := ErrorText + #13#10 + '已停止自动启动。设置备份：' + BackupDirectory;
    Log(ErrorText);
    SuppressibleMsgBox(ErrorText, mbError, MB_OK, IDOK);
  end;
end;

function GetCustomSetupExitCode(): Integer;
begin
  Result := 0;
  if RollbackFailed then Result := 20;
end;

function OpenProcess(DesiredAccess: Cardinal; InheritHandle: BOOL; ProcessId: Cardinal): THandle;
  external 'OpenProcess@kernel32.dll stdcall';
function WaitForSingleObject(Handle: THandle; Milliseconds: Cardinal): Cardinal;
  external 'WaitForSingleObject@kernel32.dll stdcall';
function CloseHandle(Handle: THandle): BOOL;
  external 'CloseHandle@kernel32.dll stdcall';

function WaitForLauncherExit(): Boolean;
var
  ProcessIdText, ErrorText: String;
  ProcessId: Integer;
  ProcessHandle: THandle;
  ErrorCode, WaitResult: Cardinal;
begin
  Result := True;
  ProcessIdText := ExpandConstant('{param:LAUNCHERPID|}');
  if ProcessIdText = '' then Exit;

  ErrorText := '';
  ProcessId := StrToIntDef(ProcessIdText, 0);
  if ProcessId <= 0 then
    ErrorText := '启动器更新参数无效，安装已停止。'
  else begin
    ProcessHandle := OpenProcess(SYNCHRONIZE, False, ProcessId);
    if ProcessHandle = 0 then begin
      ErrorCode := DLLGetLastError();
      if ErrorCode <> ERROR_INVALID_PARAMETER then
        ErrorText := '检查启动器退出状态失败：' + SysErrorMessage(ErrorCode);
    end
    else begin
      try
        Log('Waiting for launcher process to exit.');
        WaitResult := WaitForSingleObject(ProcessHandle, 30000);
        if WaitResult = WAIT_TIMEOUT then
          ErrorText := '启动器退出超时，安装已停止。请关闭启动器后重试。'
        else if WaitResult <> WAIT_OBJECT_0 then
          ErrorText := '等待启动器退出失败：' + SysErrorMessage(DLLGetLastError());
      finally
        CloseHandle(ProcessHandle);
      end;
    end;
  end;

  Result := ErrorText = '';
  if not Result then begin
    Log(ErrorText);
    SuppressibleMsgBox(ErrorText, mbError, MB_OK, IDOK);
  end;
end;

function InitializeSetup(): Boolean;
var
  RollbackOption, ResetOption: String;
begin
  RollbackOption := ExpandConstant('{param:ROLLBACK|0}');
  ResetOption := ExpandConstant('{param:RESETSETTINGS|0}');
  RollbackActive := RollbackOption = '1';
  ResetSettings := ResetOption = '1';
  UpdateChannel := ExpandConstant('{param:UPDATECHANNEL|}');
  if ((RollbackOption <> '0') and (RollbackOption <> '1')) or
    ((ResetOption <> '0') and (ResetOption <> '1')) or
    (ResetSettings and not RollbackActive) or (RollbackActive and
    ((ExpandConstant('{param:LAUNCHERPID|}') = '') or
     ((UpdateChannel <> 'stable') and (UpdateChannel <> 'preview')))) then begin
    SuppressibleMsgBox('回退安装参数无效，安装已停止。', mbError, MB_OK, IDOK);
    Result := False;
    Exit;
  end;
  DataDirectory := ExpandConstant('{#RollbackDataDirectory}');
  SettingsNames[0] := 'settings.json';
  SettingsNames[1] := 'settings.json.bak';
  SettingsNames[2] := 'update-settings.json';
  SettingsNames[3] := 'update-settings.json.bak';
  Result := IsDotNetInstalled(net48, 0);
  if Result then Result := WaitForLauncherExit();
end;
