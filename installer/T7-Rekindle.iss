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
MinVersion=10.0.19045

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
Filename: "{app}\{#MyAppExeName}"; Description: "运行 T7-Rekindle"; Flags: postinstall nowait skipifsilent
Filename: "{app}"; Description: "打开安装目录"; Flags: postinstall shellexec skipifsilent unchecked

[Code]
function InitializeSetup(): Boolean;
begin
  Result := IsDotNetInstalled(net48, 0);
end;
