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
Filename: "{app}\{#MyAppExeName}"; Description: "运行 T7-Rekindle"; Flags: postinstall nowait
Filename: "{app}"; Description: "打开安装目录"; Flags: postinstall shellexec skipifsilent unchecked

[Code]
const
  SYNCHRONIZE = $00100000;
  WAIT_OBJECT_0 = 0;
  WAIT_TIMEOUT = 258;
  ERROR_INVALID_PARAMETER = 87;

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
begin
  Result := IsDotNetInstalled(net48, 0);
  if Result then Result := WaitForLauncherExit();
end;
