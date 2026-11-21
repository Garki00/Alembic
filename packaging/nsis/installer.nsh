; ALEMBIC assisted-NSIS UI integration.
;
; NSIS keeps ownership of extraction, registry writes, shortcuts and the
; post-install PowerShell step. A small x86 Unicode Win32/GDI+ helper owns the
; visible frameless wizard, so the installer matches the application's look
; without replacing any installation logic.
;
; This file is injected by electron-builder *before* its own templates, so any
; define or macro taken from those templates has to be provided here as well.

!include "nsDialogs.nsh"
!include "LogicLib.nsh"
!include "FileFunc.nsh"
!include "WinMessages.nsh"

; Same values as electron-builder's multiUser.nsh, declared early because this
; include is processed before that file.
!define /IfNDef INSTALL_REGISTRY_KEY "Software\${APP_GUID}"
!define /IfNDef UNINSTALL_REGISTRY_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${UNINSTALL_APP_KEY}"

; Paths written by the post-install script (it derives them from the executable
; name, so the dev build's "开发版" names are handled without duplication).
!define /IfNDef ALEMBIC_UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${PRODUCT_FILENAME}"
!define /IfNDef ALEMBIC_INSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\App Paths\${PRODUCT_FILENAME}.exe"

!ifdef COMPANY_NAME
  !define ALEMBIC_PUBLISHER "${COMPANY_NAME}"
!else
  !define ALEMBIC_PUBLISHER "Garki"
!endif

; Built by packaging/tools/make-nsis-assets.ps1 before electron-builder runs.
!define /IfNDef ALEMBIC_UI_DLL "${__FILEDIR__}\..\..\desktop\build\nsis\x86-unicode\alembic-installer-ui.dll"
!define ALEMBIC_UI_OPTIONS "$PLUGINSDIR\alembic-ui.ini"

!ifndef BUILD_UNINSTALLER
Var NsisDesktopShortcutState
Var NsisStartMenuShortcutState

Function NsisHideWizard
  System::Call 'user32::ShowWindow(p $HWNDPARENT, i 0) i .r0'
FunctionEnd

Function NsisShowWizard
  System::Call 'user32::ShowWindow(p $HWNDPARENT, i 5) i .r0'
FunctionEnd

Function NsisNativeUiCreate
  Call NsisHideWizard
  System::Call '"$PLUGINSDIR\alembic-installer-ui.dll"::AlembicUI_Create(p $HWNDPARENT, w "$INSTDIR", w "${ALEMBIC_UI_OPTIONS}") i .r0'
  ${If} $0 != 1
    Call NsisShowWizard
    MessageBox MB_ICONSTOP|MB_OK "无法启动 ALEMBIC 安装界面，安装已取消。"
    Quit
  ${EndIf}
FunctionEnd

Function NsisNativeUiMode
  ; The mode arrives in $0 (System::Call register r0) so no stack juggling is needed.
  System::Call '"$PLUGINSDIR\alembic-installer-ui.dll"::AlembicUI_SetMode(i r0)'
FunctionEnd

!macro NsisSetUiMode mode
  Push $0
  StrCpy $0 ${mode}
  Call NsisNativeUiMode
  Pop $0
!macroend

Function NsisOptionsPageCreate
  nsDialogs::Create 1018
  Pop $0
  ${If} $0 == error
    Abort
  ${EndIf}

  Call NsisNativeUiCreate
  nsDialogs::Show
FunctionEnd

Function NsisOptionsPageLeave
  ; The native page has already written its result to a UTF-16 INI file, so no
  ; large Unicode buffer has to cross the plugin ABI.
  ReadINIStr $INSTDIR "${ALEMBIC_UI_OPTIONS}" "install" "directory"
  ReadINIStr $NsisDesktopShortcutState "${ALEMBIC_UI_OPTIONS}" "install" "desktop"
  ReadINIStr $NsisStartMenuShortcutState "${ALEMBIC_UI_OPTIONS}" "install" "startmenu"
  ${If} $NsisDesktopShortcutState == ""
    StrCpy $NsisDesktopShortcutState "1"
  ${EndIf}
  ${If} $NsisStartMenuShortcutState == ""
    StrCpy $NsisStartMenuShortcutState "1"
  ${EndIf}

  ; Drop wrapping quotes a user may have pasted together with the path.
  StrCpy $1 $INSTDIR 1
  ${If} $1 == '"'
    StrCpy $INSTDIR $INSTDIR -1 1
    StrCpy $1 $INSTDIR 1 -1
    ${If} $1 == '"'
      StrCpy $INSTDIR $INSTDIR -1
    ${EndIf}
  ${EndIf}

  ${If} $INSTDIR == ""
    !insertmacro NsisSetUiMode 0
    MessageBox MB_ICONEXCLAMATION|MB_OK "请选择安装位置。"
    Abort
  ${EndIf}

  ; Must already be a local absolute path ("X:\...") before normalization, so a
  ; relative path cannot silently resolve against $PLUGINSDIR.
  StrCpy $1 $INSTDIR 1 1
  ${If} $1 != ":"
    !insertmacro NsisSetUiMode 0
    MessageBox MB_ICONEXCLAMATION|MB_OK "请输入完整的本地路径，例如 D:\ALEMBIC。"
    Abort
  ${EndIf}

  GetFullPathName $INSTDIR "$INSTDIR"
  StrCpy $2 $INSTDIR 1 -1
  ${If} $2 == "\"
    StrCpy $INSTDIR $INSTDIR -1
  ${EndIf}
  ; "C:\", "C:\Windows\.." and similar normalize to something that must not be
  ; accepted as an installation directory.
  StrLen $1 $INSTDIR
  ${If} $1 < 4
    !insertmacro NsisSetUiMode 0
    MessageBox MB_ICONEXCLAMATION|MB_OK "请选择具体安装目录，例如 D:\ALEMBIC。"
    Abort
  ${EndIf}
  StrCpy $2 $INSTDIR 1 -1
  ${If} $2 == ":"
    !insertmacro NsisSetUiMode 0
    MessageBox MB_ICONEXCLAMATION|MB_OK "请选择具体安装目录，而不是驱动器根目录。"
    Abort
  ${EndIf}

  ${If} $INSTDIR == "$PROGRAMFILES64"
  ${OrIf} $INSTDIR == "$PROGRAMFILES64\"
  ${OrIf} $INSTDIR == "$PROGRAMFILES"
  ${OrIf} $INSTDIR == "$PROGRAMFILES\"
  ${OrIf} $INSTDIR == "$WINDIR"
  ${OrIf} $INSTDIR == "$WINDIR\"
  ${OrIf} $INSTDIR == "$SYSDIR"
  ${OrIf} $INSTDIR == "$SYSDIR\"
    !insertmacro NsisSetUiMode 0
    MessageBox MB_ICONEXCLAMATION|MB_OK "请选择具体安装目录，而不是系统目录本身。"
    Abort
  ${EndIf}

  ; Never wipe an unrelated non-empty directory.
  IfFileExists "$INSTDIR\*.*" alembic_path_has_files
  Goto alembic_path_ok
alembic_path_has_files:
  IfFileExists "$INSTDIR\${PRODUCT_FILENAME}.exe" alembic_path_ok
  IfFileExists "$INSTDIR\${PRODUCT_FILENAME}-Uninstall.exe" alembic_path_ok
  !insertmacro NsisSetUiMode 0
  MessageBox MB_ICONEXCLAMATION|MB_OK "该目录已有其他文件，请选择空目录或现有的 ALEMBIC 安装目录。"
  Abort
alembic_path_ok:
  Call AlembicPrepareInstall
FunctionEnd

Function AlembicInstallFilesPagePre
  Call NsisHideWizard
FunctionEnd

Function AlembicInstallFilesPageShow
  ; The built-in NSIS instfiles page still performs and owns the installation.
  !insertmacro NsisSetUiMode 2
  Call NsisHideWizard
FunctionEnd

Function NsisFinishPageCreate
  nsDialogs::Create 1018
  Pop $0
  ${If} $0 == error
    Abort
  ${EndIf}
  !insertmacro NsisSetUiMode 4
  Call NsisHideWizard
  nsDialogs::Show
FunctionEnd

Function NsisFinishPageLeave
  System::Call '"$PLUGINSDIR\alembic-installer-ui.dll"::AlembicUI_Shutdown()'
FunctionEnd

Function AlembicPrepareInstall
  ; Stop the running app, then replace only an existing ALEMBIC installation.
  nsExec::Exec 'taskkill /IM "${PRODUCT_FILENAME}.exe" /T /F'
  Pop $0
  nsExec::Exec 'taskkill /IM "alembic-backend.exe" /T /F'
  Pop $0

  DeleteRegKey HKLM "${UNINSTALL_REGISTRY_KEY}"
  !ifdef UNINSTALL_REGISTRY_KEY_2
    DeleteRegKey HKLM "${UNINSTALL_REGISTRY_KEY_2}"
  !endif
  DeleteRegKey HKLM "${INSTALL_REGISTRY_KEY}"
  DeleteRegKey HKLM "${ALEMBIC_UNINSTALL_KEY}"
  DeleteRegKey HKLM "${ALEMBIC_INSTALL_KEY}"

  RMDir /r "$INSTDIR"
  ; Legacy folder produced by earlier development builds.
  RMDir /r "$PROGRAMFILES64\alembic-desktop"
  CreateDirectory "$INSTDIR"
FunctionEnd

Function .onGUIEnd
  ${IfNot} ${Silent}
    System::Call '"$PLUGINSDIR\alembic-installer-ui.dll"::AlembicUI_Shutdown()'
  ${EndIf}
FunctionEnd

!macro customInit
  InitPluginsDir
  File /oname=$PLUGINSDIR\alembic-installer-ui.dll "${ALEMBIC_UI_DLL}"

  ; Preserve /D and electron-builder's computed install directory.
  ${If} $INSTDIR == ""
    StrCpy $INSTDIR "$PROGRAMFILES64\${PRODUCT_FILENAME}"
  ${EndIf}
  ${If} $INSTDIR == "$PROGRAMFILES64\alembic-desktop"
    StrCpy $INSTDIR "$PROGRAMFILES64\${PRODUCT_FILENAME}"
  ${EndIf}

  ${If} $NsisDesktopShortcutState == ""
    StrCpy $NsisDesktopShortcutState "1"
  ${EndIf}
  ${If} $NsisStartMenuShortcutState == ""
    StrCpy $NsisStartMenuShortcutState "1"
  ${EndIf}

  IfSilent alembic_silent_prepare
  Goto alembic_prepare_done
alembic_silent_prepare:
  Call AlembicPrepareInstall
alembic_prepare_done:
!macroend

; electron-builder inserts this hook right before MUI_PAGE_INSTFILES.
!macro customPageAfterChangeDir
  !define /IfNDef MUI_PAGE_CUSTOMFUNCTION_PRE AlembicInstallFilesPagePre
  !define /IfNDef MUI_PAGE_CUSTOMFUNCTION_SHOW AlembicInstallFilesPageShow
!macroend

!macro customWelcomePage
  Page custom NsisOptionsPageCreate NsisOptionsPageLeave
!macroend

!macro customInstall
  ; electron-builder's stub uninstaller and its uninstall entry are replaced by
  ; the application uninstaller written by nsis-postinstall.ps1.
  Delete "$INSTDIR\Uninstall ${PRODUCT_FILENAME}.exe"
  DeleteRegKey HKLM "${UNINSTALL_REGISTRY_KEY}"
  !ifdef UNINSTALL_REGISTRY_KEY_2
    DeleteRegKey HKLM "${UNINSTALL_REGISTRY_KEY_2}"
  !endif

  ${IfNot} ${Silent}
    !insertmacro NsisSetUiMode 3
  ${EndIf}

  DetailPrint "配置运行时与命令沙盒…"
  nsExec::ExecToStack '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "$INSTDIR\resources\install\nsis-postinstall.ps1" -InstallDir "$INSTDIR" -AppExe "${PRODUCT_FILENAME}.exe" -Version "${VERSION}" -Publisher "${ALEMBIC_PUBLISHER}" -DesktopShortcut "$NsisDesktopShortcutState" -StartMenuShortcut "$NsisStartMenuShortcutState" -LogFile "$TEMP\alembic-install.log"'
  Pop $0
  Pop $1
  ${If} $0 != 0
    MessageBox MB_ICONSTOP|MB_OK "安装配置失败。$\r$\n$1"
    RMDir /r "$INSTDIR"
    Abort
  ${EndIf}
!macroend

!macro customFinishPage
  Page custom NsisFinishPageCreate NsisFinishPageLeave
!macroend
!endif
