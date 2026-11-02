#ifndef BundleDir
  #error BundleDir is required
#endif
#ifndef Bootstrapper
  #error Bootstrapper is required
#endif
#ifndef OutputDir
  #error OutputDir is required
#endif
#ifndef AppVersion
  #define AppVersion "0.2.0"
#endif
#ifndef ResourceDir
  #define ResourceDir SourcePath
#endif

[Setup]
AppId={{CCEA795F-E245-4BDA-976E-5C72E2BA9761}
AppName=文枢
AppVersion={#AppVersion}
AppPublisher=文枢
AppComments=本地资料阅读与写作工作台
DefaultDirName={localappdata}\Programs\Wenshu
DefaultGroupName=文枢
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
MinVersion=10.0
OutputDir={#OutputDir}
OutputBaseFilename=Wenshu-Setup-{#AppVersion}-x64
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\Wenshu.exe
AppMutex=Local\Wenshu.Desktop,Local\Wenshu.Desktop.Services
CloseApplications=no
RestartApplications=no
SetupLogging=yes

[Languages]
Name: "zhcn"; MessagesFile: "compiler:Default.isl,{#ResourceDir}\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："

[Files]
Source: "{#BundleDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#Bootstrapper}"; DestName: "MicrosoftEdgeWebview2Setup.exe"; Flags: dontcopy

[Icons]
Name: "{group}\文枢"; Filename: "{app}\Wenshu.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\文枢"; Filename: "{app}\Wenshu.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\Wenshu.exe"; Description: "启动文枢"; Flags: nowait postinstall skipifsilent

[Code]
const
  RuntimeRegistryPath = 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';

function RuntimeVersionPresent(RootKey: Integer): Boolean;
var
  Version: String;
  I, DotCount: Integer;
begin
  Result := False;
  if not RegQueryStringValue(RootKey, RuntimeRegistryPath, 'pv', Version) then Exit;
  if (Version = '') or (Version = '0.0.0.0') then Exit;
  if (Version[1] = '.') or (Version[Length(Version)] = '.') or (Pos('..', Version) > 0) then Exit;
  DotCount := 0;
  for I := 1 to Length(Version) do begin
    if Version[I] = '.' then DotCount := DotCount + 1
    else if (Version[I] < '0') or (Version[I] > '9') then Exit;
  end;
  Result := DotCount = 3;
end;

function IsWebView2Installed(): Boolean;
begin
  { Microsoft documents HKCU and the 32-bit HKLM view for this Runtime GUID. }
  Result := RuntimeVersionPresent(HKCU) or RuntimeVersionPresent(HKCU32) or
            RuntimeVersionPresent(HKLM32) or RuntimeVersionPresent(HKLM64);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ExitCode, Choice: Integer;
  Launched: Boolean;
begin
  Result := '';
  if IsWebView2Installed() then begin
    Log('WebView2 Runtime is already present; reusing it.');
    Exit;
  end;
  ExtractTemporaryFile('MicrosoftEdgeWebview2Setup.exe');
  repeat
    WizardForm.StatusLabel.Caption := '正在下载并安装 Microsoft Edge WebView2，请保持网络连接……';
    Launched := Exec(ExpandConstant('{tmp}\MicrosoftEdgeWebview2Setup.exe'),
                     '/silent /install', '', SW_HIDE, ewWaitUntilTerminated, ExitCode);
    Log(Format('WebView2 bootstrapper launch=%d exit=%d', [Ord(Launched), ExitCode]));
    if IsWebView2Installed() then begin
      Result := '';
      Exit;
    end;
    Result := '尚未完成 WebView2 安装。请检查网络和系统安装策略后重试；文枢尚未安装。';
    if WizardSilent() then Exit;
    Choice := MsgBox(Result, mbError, MB_RETRYCANCEL);
    if Choice <> IDRETRY then Exit;
  until False;
end;

function InitializeUninstall(): Boolean;
begin
  { AppMutex prevents uninstall while the desktop is open. No worker is killed. }
  Result := True;
end;

{ User data lives outside the application directory; no uninstall delete rule removes it. }
