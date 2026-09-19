; installer.iss - Inno Setup заворачивает dist\AvatariaHelper в один
; Output\AvatariaHelperSetup.exe: окно «Добро пожаловать», выбор папки,
; ярлыки и удаление через «Программы и компоненты».
;
; Собирать после PyInstaller:
;   py -m PyInstaller --noconfirm AvatariaHelper.spec
;   "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" installer.iss

#define AppName      "Avataria Helper"
#define AppVersion   "1.0.0"
#define AppPublisher "Deatser"
#define AppUrl       "https://github.com/Deatser/Avataria-Helper"
#define AppExe       "AvatariaHelper.exe"

[Setup]
AppId={{74BC7F49-CDEE-45BB-9948-4D3B068C8C58}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppUrl}
AppSupportURL={#AppUrl}/issues
; Без прав администратора: ставим в %LOCALAPPDATA%\Programs, а не в
; Program Files. Помощнику admin не нужен, а лишнее окно UAC на установке
; мода пугает сильнее, чем помогает.
PrivilegesRequired=lowest
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=Output
OutputBaseFilename=AvatariaHelperSetup
SetupIconFile=assets\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; В Inno 6 страница приветствия по умолчанию выключена, а она тут и нужна -
; «Вас приветствует мастер установки Avataria Helper».
DisableWelcomePage=no
; Данные (config.json, stats.json, logs) живут в %APPDATA%\Avataria Helper
; и переустановку переживают - см. app/core/paths.py.

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "dist\AvatariaHelper\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#StringChange(AppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
