; ALEMBIC assisted installer integration using the stock Modern UI2 wizard.
;
; The custom frameless window (packaging/installer-ui) is archived and not used.
; Everything here relies on standard NSIS/MUI pages so the installer keeps the
; native look, the project icon and predictable behaviour.

!include "nsDialogs.nsh"
!include "LogicLib.nsh"
!include "WinMessages.nsh"

; Same values as electron-builder's multiUser.nsh, declared early because this
; include is processed before that file.
!define /IfNDef INSTALL_REGISTRY_KEY "Software\${APP_GUID}"
!define /IfNDef UNINSTALL_REGISTRY_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${UNINSTALL_APP_KEY}"

; Paths written by the post-install script (derived from the executable name,
; so the dev build's "开发版" names are handled without duplication).
!define /IfNDef ALEMBIC_UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${PRODUCT_FILENAME}"
!define /IfNDef ALEMBIC_INSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\App Paths\${PRODUCT_FILENAME}.exe"

!ifdef COMPANY_NAME
  !define ALEMBIC_PUBLISHER "${COMPANY_NAME}"
!else
  !define ALEMBIC_PUBLISHER "Garki"
!endif

!ifndef BUILD_UNINSTALLER
Var NsisDesktopShortcutState
Var NsisStartMenuShortcutState
Var NsisDesktopCheckbox
Var NsisStartMenuCheckbox

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

; Shown right after the standard directory page.
Function NsisOptionsPageCreate
  nsDialogs::Create 1018
  Pop $0
  ${If} $0 == error
    Abort
  ${EndIf}

  ; MUI_HEADER_TEXT cannot be used here: this include is preprocessed before
  ; MUI2.nsh is loaded, so the title is drawn as a normal label instead.
  ${NSD_CreateLabel} 0 0 100% 14u "安装选项"
  Pop $0
  CreateFont $1 "Microsoft YaHei UI" "12" "700"
  SendMessage $0 ${WM_SETFONT} $1 1

  ${NSD_CreateLabel} 0 18u 100% 34u "安装器将安装 ALEMBIC 主程序、内置运行时（Node、Python、pnpm）以及 Windows 命令沙盒。$\r$\n运行时与命令沙盒是必需组件，不能取消安装。"
  Pop $0

  ${NSD_CreateCheckbox} 0 60u 100% 12u "创建桌面快捷方式"
  Pop $NsisDesktopCheckbox
  ${NSD_SetState} $NsisDesktopCheckbox 1

  ${NSD_CreateCheckbox} 0 78u 100% 12u "创建开始菜单快捷方式"
  Pop $NsisStartMenuCheckbox
  ${NSD_SetState} $NsisStartMenuCheckbox 1

  nsDialogs::Show
FunctionEnd

Function NsisOptionsPageLeave
  ${NSD_GetState} $NsisDesktopCheckbox $0
  ${If} $0 == 1
    StrCpy $NsisDesktopShortcutState "1"
  ${Else}
    StrCpy $NsisDesktopShortcutState "0"
  ${EndIf}

  ${NSD_GetState} $NsisStartMenuCheckbox $0
  ${If} $0 == 1
    StrCpy $NsisStartMenuShortcutState "1"
  ${Else}
    StrCpy $NsisStartMenuShortcutState "0"
  ${EndIf}
FunctionEnd

!macro customInit
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

; electron-builder inserts this hook right before MUI_PAGE_INSTFILES, i.e. after
; the directory page that allowToChangeInstallationDirectory enables.
!macro customPageAfterChangeDir
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

  DetailPrint "配置运行时与命令沙盒…"
  ; NSIS is a 32-bit process, so "$SYSDIR\WindowsPowerShell" would start the
  ; 32-bit PowerShell. That one has no LocalAccounts module (Get-LocalUser) and
  ; redirects HKLM\SOFTWARE to Wow6432Node. Sysnative addresses the 64-bit
  ; PowerShell from a 32-bit process.
  StrCpy $2 "$WINDIR\Sysnative\WindowsPowerShell\v1.0\powershell.exe"
  IfFileExists "$2" alembic_powershell_ready
  StrCpy $2 "$SYSDIR\WindowsPowerShell\v1.0\powershell.exe"
alembic_powershell_ready:
  nsExec::ExecToStack '"$2" -NoProfile -ExecutionPolicy Bypass -File "$INSTDIR\resources\install\nsis-postinstall.ps1" -InstallDir "$INSTDIR" -AppExe "${PRODUCT_FILENAME}.exe" -Version "${VERSION}" -Publisher "${ALEMBIC_PUBLISHER}" -DesktopShortcut "$NsisDesktopShortcutState" -StartMenuShortcut "$NsisStartMenuShortcutState" -LogFile "$TEMP\alembic-install.log"'
  Pop $0
  Pop $1
  ${If} $0 != 0
    MessageBox MB_ICONSTOP|MB_OK "安装配置失败。$\r$\n$1"
    RMDir /r "$INSTDIR"
    Abort
  ${EndIf}
!macroend
!endif
