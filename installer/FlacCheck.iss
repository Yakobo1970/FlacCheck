; FlacCheck Setup - Inno Setup Skript
;
; Packt das mit PyInstaller gebaute, eigenstaendige dist\FlacCheck\-Verzeichnis (fertige .exe
; samt Python, Qt, FFmpeg, libsndfile, PortAudio) in einen einzelnen Installer.
;
; Vor dem Bauen erst die .exe erzeugen (in der Conda-Umgebung "flaccheck"):
;   python -m PyInstaller --noconfirm FlacCheck.spec
;
; Dann bauen:
;   "C:\Program Files\Inno Setup 7\ISCC.exe" installer\FlacCheck.iss
;
; Bei neuen Versionen AppId beibehalten (sonst gilt es als anderes Programm), nur AppVersion anheben.

#define AppName "FlacCheck"
#define AppVersion "1.0.2"
#define AppPublisher "yakobo1970"
#define AppURL "https://www.undabtanzbar.de/software/flaccheck/"
#define AppExeName "FlacCheck.exe"

[Setup]
AppId={{FB13FB7F-9E40-4B02-AF6C-926126567F92}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL=https://github.com/Yakobo1970/FlacCheck/releases
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\{#AppExeName}
SetupIconFile=..\assets\icon.ico
OutputDir=..\dist_installer
OutputBaseFilename={#AppName}-{#AppVersion}-Setup
Compression=lzma2/max
SolidCompression=yes
; Installiert pro Benutzer - keine Admin-Rechte noetig. Die Datenbank liegt unabhaengig davon in
; %LOCALAPPDATA%\FlacCheck und bleibt beim Deinstallieren erhalten.
PrivilegesRequired=lowest
LicenseFile=..\LICENSE
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "german"; MessagesFile: "compiler:Languages\German.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\{#AppName}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
