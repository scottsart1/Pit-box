#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\YourPitBox NASCAR"
#endif
#ifndef OutputDir
  #define OutputDir "..\build\artifacts"
#endif
[Setup]
AppId={{0E783D12-B231-483D-91B6-80208E7EA21E}
AppName=YourPitBox NASCAR
AppVersion={#AppVersion}
AppPublisher=YourPitBox
DefaultDirName={localappdata}\Programs\YourPitBox NASCAR
DefaultGroupName=YourPitBox NASCAR
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
OutputDir={#OutputDir}
OutputBaseFilename=YourPitBox-NASCAR-Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no
UninstallDisplayIcon={app}\YourPitBox NASCAR.exe
LicenseFile=..\NOTICE.txt
[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"
[Icons]
Name: "{autoprograms}\YourPitBox NASCAR"; Filename: "{app}\YourPitBox NASCAR.exe"
Name: "{autodesktop}\YourPitBox NASCAR"; Filename: "{app}\YourPitBox NASCAR.exe"; Tasks: desktopicon
[Run]
Filename: "{app}\YourPitBox NASCAR.exe"; Description: "Open YourPitBox NASCAR"; Flags: nowait postinstall skipifsilent
[Messages]
WelcomeLabel2=This installs the separate YourPitBox NASCAR workspace and crew chief.%n%nThe Windows app can read calibrated HUD regions, accept manual observations and import practice laps. Native NASCAR 26 telemetry support and a full game-session test remain unverified in this development build.%n%nThe Android companion connects to this PC. This installer does not modify the F1 app or its history.
; Driver sessions in %USERPROFILE%\YourPitBoxNASCAR survive uninstall.
