Unicode True
ManifestSupportedOS win10
RequestExecutionLevel user

!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "nsDialogs.nsh"
!include "FileFunc.nsh"
!include "WinVer.nsh"

!ifndef APP_VERSION
  !define APP_VERSION "1.4.0"
!endif
!ifndef PAYLOAD
  !define PAYLOAD "..\artifacts\Qwasda-${APP_VERSION}-x64.exe"
!endif
!ifndef OUT_DIR
  !define OUT_DIR "..\artifacts"
!endif

Name "Qwasda ${APP_VERSION}"
OutFile "${OUT_DIR}\Qwasda-Setup-${APP_VERSION}-x64.exe"
InstallDir "$LOCALAPPDATA\Programs\Qwasda"
InstallDirRegKey HKCU "Software\Qwasda" "InstallDir"
BrandingText "Qwasda — українсько-англійський перемикач розкладки"
ShowInstDetails show
ShowUnInstDetails show

VIProductVersion "${APP_VERSION}.0"
VIAddVersionKey "ProductName" "Qwasda"
VIAddVersionKey "ProductVersion" "${APP_VERSION}"
VIAddVersionKey "FileVersion" "${APP_VERSION}.0"
VIAddVersionKey "CompanyName" "Qwasda contributors"
VIAddVersionKey "FileDescription" "Qwasda installer"
VIAddVersionKey "LegalCopyright" "MIT License"

!define MUI_ABORTWARNING
!define MUI_ICON "..\assets\qwasda.ico"
!define MUI_UNICON "..\assets\qwasda.ico"
!define MUI_WELCOMEPAGE_TITLE "Встановлення Qwasda"
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_TEXT "Запустити Qwasda"
!define MUI_FINISHPAGE_RUN_FUNCTION LaunchQwasda

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "..\LICENSE"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

Var ExistingRunCommand
Var InstallAutostart

Function .onInit
  ReadRegStr $ExistingRunCommand HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "Qwasda"
  StrCpy $InstallAutostart "0"
  ${GetParameters} $0
  ${GetOptions} $0 "/AUTOSTART" $1
  ${If} $1 != ""
    StrCpy $InstallAutostart "1"
  ${EndIf}
FunctionEnd

Function LaunchQwasda
  Exec '"$INSTDIR\Qwasda.exe"'
FunctionEnd

Section "Qwasda" SEC_CORE
  SectionIn RO
  SetOutPath "$INSTDIR"
  File /oname=Qwasda.exe "${PAYLOAD}"

  CreateDirectory "$SMPROGRAMS\Qwasda"
  CreateShortCut "$SMPROGRAMS\Qwasda\Qwasda.lnk" "$INSTDIR\Qwasda.exe"
  CreateShortCut "$SMPROGRAMS\Qwasda\Uninstall Qwasda.lnk" "$INSTDIR\Uninstall.exe"

  WriteRegStr HKCU "Software\Qwasda" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Qwasda" "DisplayName" "Qwasda"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Qwasda" "DisplayVersion" "${APP_VERSION}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Qwasda" "Publisher" "Qwasda contributors"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Qwasda" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Qwasda" "DisplayIcon" "$INSTDIR\Qwasda.exe"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Qwasda" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Qwasda" "QuietUninstallString" '"$INSTDIR\Uninstall.exe" /S'
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Qwasda" "NoModify" 1
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Qwasda" "NoRepair" 0
  WriteUninstaller "$INSTDIR\Uninstall.exe"

  ${If} $ExistingRunCommand != ""
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "Qwasda" $ExistingRunCommand
  ${EndIf}
  ${If} $InstallAutostart == "1"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "Qwasda" '"$INSTDIR\Qwasda.exe"'
  ${EndIf}
SectionEnd

Section /o "Автозапуск Windows" SEC_AUTOSTART
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "Qwasda" '"$INSTDIR\Qwasda.exe"'
SectionEnd

Var PurgeData
Var PurgeCheckbox

Function un.PurgePage
  ${If} ${Silent}
    Abort
  ${EndIf}
  nsDialogs::Create 1018
  Pop $0
  ${If} $0 == error
    Abort
  ${EndIf}
  ${NSD_CreateCheckbox} 0 10u 100% 12u "Видалити налаштування та користувацькі дані"
  Pop $PurgeCheckbox
  ${NSD_SetState} $PurgeCheckbox ${BST_UNCHECKED}
  nsDialogs::Show
FunctionEnd

Function un.PurgePageLeave
  ${NSD_GetState} $PurgeCheckbox $PurgeData
FunctionEnd

Function un.onInit
  StrCpy $PurgeData ${BST_UNCHECKED}
  ${GetParameters} $0
  ${GetOptions} $0 "/PURGEUSERDATA" $1
  ${If} $1 != ""
    StrCpy $PurgeData ${BST_CHECKED}
  ${EndIf}
FunctionEnd

!insertmacro MUI_UNPAGE_CONFIRM
UninstPage custom un.PurgePage un.PurgePageLeave
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "Ukrainian"
!insertmacro MUI_LANGUAGE "English"

Section "Uninstall"
  Delete "$SMPROGRAMS\Qwasda\Qwasda.lnk"
  Delete "$SMPROGRAMS\Qwasda\Uninstall Qwasda.lnk"
  RMDir "$SMPROGRAMS\Qwasda"

  ReadRegStr $0 HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "Qwasda"
  ${If} $0 != ""
    StrCmp $0 '"$INSTDIR\Qwasda.exe"' 0 +2
      DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "Qwasda"
  ${EndIf}

  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Qwasda"
  DeleteRegKey HKCU "Software\Qwasda"
  Delete "$INSTDIR\Qwasda.exe"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"

  ${If} $PurgeData == ${BST_CHECKED}
    RMDir /r "$APPDATA\Qwasda"
    RMDir /r "$LOCALAPPDATA\Qwasda"
  ${EndIf}
SectionEnd
