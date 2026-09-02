; Inno Setup script for the Your Pit Box Windows installer.
;
; Produces a single PitWall-Setup.exe: the buyer downloads it, double-clicks,
; clicks through, and the app starts. Afterwards it is a Start Menu entry and
; a desktop icon like any other program — no setup steps ever again.
;
; Build it with the driver, which fills in the paths and version:
;     python -m distribution.packaging.build --installer
;
; Two deliberate choices:
;
;   * Installs per-user into %LOCALAPPDATA%\Programs, so there is no UAC
;     prompt. A hobby app asking for administrator rights is the point where
;     a cautious buyer stops, and nothing here needs machine-wide access.
;
;   * Never touches %USERPROFILE%\PitWallData on uninstall. That folder holds
;     the driver's recorded sessions and their licence. Removing it would
;     destroy race history and burn their one activation on a reinstall.

#ifndef AppVersion
  #define AppVersion "4.6.3"
#endif
#ifndef SourceDir
  #define SourceDir "..\..\build\dist\Your Pit Box"
#endif
#ifndef OutputDir
  #define OutputDir "..\..\build\artifacts"
#endif

#define AppName "Your Pit Box"
#define AppPublisher "Your Pit Box"
#define AppExeName "Your Pit Box.exe"

[Setup]
AppId={{8C4B1E77-2A5D-4F3E-9B61-0D7A2F5C8E14}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; Per-user install: no administrator prompt.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir={#OutputDir}
OutputBaseFilename=PitWall-Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; 64-bit only, matching the Python build.
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile={#SourceDir}\EULA.txt
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExeName}
; Upgrading while Your Pit Box is running would leave a half-written install: the
; executable is locked by the live process. Restart Manager detects which files
; are in use and offers to close them, which is the guard a buyer actually
; sees. RestartApplications is off because [Run] already relaunches the app at
; the end of setup, and the pair together would start it twice.
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
; The whole PyInstaller one-folder build. Order matters only in that the exe
; must exist before [Run] fires.
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
; Ticked by default: the last click of the installer is the first launch, so
; activation happens immediately while the buyer still has the code in front
; of them, rather than being a separate step they have to discover.
Filename: "{app}\{#AppExeName}"; Description: "Start {#AppName} now"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Remove only what the installer created. PitWallData is intentionally absent.
Type: filesandordirs; Name: "{app}\_internal"

[Messages]
WelcomeLabel2=This will install [name/ver] on your computer.%n%nYour Pit Box is free: there is no activation code. The first time it runs it asks for an OpenAI API key, which you can also add later from the Connection tab.
FinishedLabelNoIcons=Setup has finished installing [name].
FinishedLabel=Setup has finished installing [name].%n%nThe first time it starts, paste your OpenAI API key or choose Skip for now. After that it opens straight to the dashboard.

[Code]
// Running copies are handled by CloseApplications in [Setup], not here. An
// earlier version of this file ran tasklist.exe and discarded the result, so
// the "refuse to install over a running copy" it claimed never happened.

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  // Silent uninstalls must stay silent: a modal box with no one to click it
  // hangs the uninstaller until the process is killed.
  if (CurUninstallStep = usPostUninstall) and not UninstallSilent then
    MsgBox('Your Pit Box has been removed.' + #13#10 + #13#10 +
           'Your recorded sessions and settings in PitWallData have been left ' +
           'in place, so reinstalling picks up where you left off.',
           mbInformation, MB_OK);
end;
