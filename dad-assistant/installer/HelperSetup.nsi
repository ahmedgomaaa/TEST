; HelperSetup.exe: one file that installs everything the helper needs on Windows 7 SP1 or newer.
;
;   1. Windows 7 without Service Pack 1 (or older Windows) -> clear message, nothing is changed.
;   2. Python 3.8 needs a Windows update (KB2533623, superseded by KB2758857). If it is missing, the
;      bundled KB2758857 is installed, the laptop restarts and setup continues by itself.
;   3. Python 3.8.10 32-bit (bundled) is installed for this user if it is missing or broken.
;   4. The app is copied, the settings page is saved to config.json (old settings are kept),
;      the Arabic word-order check runs once, shortcuts and an uninstaller are created.
;   5. The AI connection and the e-mail reports are tested and the result is shown in Arabic.
;
; Every step is logged to %TEMP%\HelperSetup.log (copied to <app>\data\logs\setup.log).
; Build with build.sh.  Silent install for testing:
;   HelperSetup.exe /S /NAME=... /APIKEY=... /EMAIL=... /SENDER=... /APPPASS=... [/NOTEST] [/FORCEKB]

Unicode true
ManifestDPIAware true
RequestExecutionLevel admin
SetCompressor /SOLID lzma

!define APPNAME "المساعد"
!define REGNAME "DadHelper"
!define VERSION "2.0.0"
!define UNINSTKEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${REGNAME}"
!define RESUME_TASK "HelperSetupResume"
!define RESUME_LINK "كمّل تثبيت المساعد"
!define PYKEY "Software\Python\PythonCore\3.8-32"
!define PYARGS 'InstallAllUsers=0 Include_launcher=0 InstallLauncherAllUsers=0 Include_test=0 Include_doc=0 Include_dev=0 Include_pip=0 Include_tcltk=1 Include_lib=1 Include_exe=1 Shortcuts=0 AssociateFiles=0 PrependPath=0 CompileAll=0'

Name "${APPNAME}"
!ifndef OUTFILE
    !define OUTFILE "dist\HelperSetup.exe"
!endif
OutFile "${OUTFILE}"
InstallDir "$LOCALAPPDATA\Programs\Helper"
InstallDirRegKey HKCU "${UNINSTKEY}" "InstallLocation"
BrandingText " "
ShowInstDetails show
ShowUninstDetails show

VIProductVersion "2.0.0.0"
VIAddVersionKey "ProductName" "Helper (Arabic computer assistant)"
VIAddVersionKey "FileDescription" "Helper setup"
VIAddVersionKey "FileVersion" "${VERSION}"
VIAddVersionKey "ProductVersion" "${VERSION}"
VIAddVersionKey "LegalCopyright" "Ahmed"

!include MUI2.nsh
!include LogicLib.nsh
!include WinVer.nsh
!include x64.nsh
!include FileFunc.nsh
!include nsDialogs.nsh
!include StrFunc.nsh
${StrStr}

Var LogH
Var PyDir
Var HadConfig
Var Rebooted
Var ForceKB
Var NoTest
Var UserName
Var ApiKey
Var MailTo
Var SmtpUser
Var SmtpPass
Var Dlg
Var hName
Var hKey
Var hTo
Var hFrom
Var hPass

; ---------------------------------------------------------------- pages

!define MUI_ICON "helper.ico"
!define MUI_UNICON "helper.ico"
!define MUI_WELCOMEFINISHPAGE_BITMAP "welcome.bmp"
!define MUI_UNWELCOMEFINISHPAGE_BITMAP "welcome.bmp"
!define MUI_ABORTWARNING
!define MUI_ABORTWARNING_TEXT "متأكد إنك عايز تلغي التثبيت؟"

!define MUI_WELCOMEPAGE_TITLE "تثبيت المساعد"
!define MUI_WELCOMEPAGE_TEXT "البرنامج ده هيثبّت المساعد على الكمبيوتر: شباك محادثة بالعربي يرد على الأسئلة ويساعد في ترتيب الملفات والصور.$\r$\n$\r$\nهيثبّت كل اللي المساعد محتاجه لوحده (بايثون وتحديث ويندوز لو ناقص). التثبيت بياخد حوالي 5 دقايق.$\r$\n$\r$\nدوس التالي عشان تبدأ."
!insertmacro MUI_PAGE_WELCOME
Page custom SettingsPage SettingsLeave
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_TITLE "التثبيت خلص"
!define MUI_FINISHPAGE_TEXT "المساعد اتثبّت. هتلاقي أيقونة اسمها $\"${APPNAME}$\" على سطح المكتب، دوس عليها مرتين عشان تفتحه."
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_TEXT "افتح المساعد دلوقتي"
!define MUI_FINISHPAGE_RUN_FUNCTION LaunchApp
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "Arabic"

; The Windows update is needed in .onInit, before the pages: put it at the start of the compressed data so
; it unpacks at once instead of after the 27 MB Python installer.
ReserveFile "payload\kb2758857-x86.msu"
ReserveFile "payload\kb2758857-x64.msu"
ReserveFile /plugin Banner.dll
ReserveFile /plugin System.dll
ReserveFile /plugin nsDialogs.dll

; ---------------------------------------------------------------- logging and errors

!macro Log text
    ${If} $LogH != ""
        FileWriteUTF16LE $LogH "${text}$\r$\n"
    ${EndIf}
!macroend
!define Log "!insertmacro Log"

; Show a message, keep the log, and stop the installation.
!macro Fail text
    ${Log} "FAILED: ${text}"
    DetailPrint "${text}"
    MessageBox MB_OK|MB_ICONSTOP "${text}$\r$\n$\r$\nالتفاصيل محفوظة في الملف:$\r$\n$TEMP\HelperSetup.log" /SD IDOK
    Call SaveLog
    SetErrorLevel 2
    Abort
!macroend
!define Fail "!insertmacro Fail"

Function OpenLog
    ${IfNot} ${FileExists} "$TEMP\HelperSetup.log"
        FileOpen $LogH "$TEMP\HelperSetup.log" w
        FileWriteWord $LogH 0xFEFF
    ${Else}
        FileOpen $LogH "$TEMP\HelperSetup.log" a
        FileSeek $LogH 0 END
    ${EndIf}
    ${GetTime} "" "L" $0 $1 $2 $3 $4 $5 $6
    ${Log} ""
    ${Log} "=== HelperSetup ${VERSION} started $2-$1-$0 $4:$5:$6 from $EXEPATH"  ; not $CMDLINE: it can hold the API key
FunctionEnd

; Copy the log next to the app's own logs so Ahmed gets it with the e-mail reports.
Function SaveLog
    ${If} $LogH != ""
        FileClose $LogH
        StrCpy $LogH ""
    ${EndIf}
    ${If} ${FileExists} "$INSTDIR\*.*"
        CreateDirectory "$INSTDIR\data\logs"
        CopyFiles /SILENT "$TEMP\HelperSetup.log" "$INSTDIR\data\logs\setup.log"
    ${EndIf}
    FileOpen $LogH "$TEMP\HelperSetup.log" a
    FileSeek $LogH 0 END
FunctionEnd

; ---------------------------------------------------------------- start-up checks

Function .onInit
    InitPluginsDir
    Call OpenLog

    ; Only one copy of the installer at a time.
    System::Call 'kernel32::CreateMutexW(p 0, i 0, w "HelperSetupMutex") p .r0 ?e'
    Pop $1
    ${If} $1 == 183
        MessageBox MB_OK|MB_ICONEXCLAMATION "برنامج التثبيت مفتوح فعلاً." /SD IDOK
        Abort
    ${EndIf}

    ; Options (used for silent test installs and after the restart).
    ${GetParameters} $R0
    ClearErrors
    ${GetOptions} $R0 "/NAME=" $UserName
    ${GetOptions} $R0 "/APIKEY=" $ApiKey
    ${GetOptions} $R0 "/EMAIL=" $MailTo
    ${GetOptions} $R0 "/SENDER=" $SmtpUser
    ${GetOptions} $R0 "/APPPASS=" $SmtpPass
    StrCpy $NoTest 0
    ClearErrors
    ${GetOptions} $R0 "/NOTEST" $0
    ${IfNot} ${Errors}
        StrCpy $NoTest 1
    ${EndIf}
    StrCpy $ForceKB 0
    ClearErrors
    ${GetOptions} $R0 "/FORCEKB" $0
    ${IfNot} ${Errors}
        StrCpy $ForceKB 1
    ${EndIf}
    StrCpy $Rebooted 0
    ClearErrors
    ${GetOptions} $R0 "/AFTERREBOOT" $0
    ${IfNot} ${Errors}
        StrCpy $Rebooted 1
        ${Log} "Resumed after the restart"
    ${EndIf}

    ; Windows version.
    ${If} ${RunningX64}
        ${Log} "Windows 64-bit"
    ${Else}
        ${Log} "Windows 32-bit"
    ${EndIf}
    ${IfNot} ${AtLeastWin7}
        ${Log} "Windows older than 7"
        MessageBox MB_OK|MB_ICONSTOP "المساعد محتاج ويندوز 7 أو أحدث. الويندوز اللي على الكمبيوتر ده أقدم من كده." /SD IDOK
        Abort
    ${EndIf}
    ${If} ${IsWin7}
    ${AndIf} ${AtMostServicePack} 0
        ${Log} "Windows 7 without Service Pack 1"
        MessageBox MB_OK|MB_ICONSTOP "ويندوز 7 اللي على الكمبيوتر ده محتاج تحديث $\"Service Pack 1$\" الأول.$\r$\n$\r$\nثبّته من Windows Update (أو اطلب من أحمد يثبّته)، وبعدين شغّل برنامج التثبيت ده تاني." /SD IDOK
        Abort
    ${EndIf}

    Call EnsureDllDirectoryUpdate
FunctionEnd

; $0 = 1 if kernel32 has AddDllDirectory (KB2533623 or a later update is installed and active).
Function HasDllDirectoryUpdate
    System::Call 'kernel32::GetModuleHandleW(w "kernel32.dll") p .r1'
    System::Call 'kernel32::GetProcAddress(p r1, m "AddDllDirectory") p .r2'
    ${If} $2 != 0
        StrCpy $0 1
    ${Else}
        StrCpy $0 0
    ${EndIf}
FunctionEnd

Function EnsureDllDirectoryUpdate
    Call HasDllDirectoryUpdate
    ${If} $0 == 1
    ${AndIf} $ForceKB != 1
        ${Log} "AddDllDirectory is present (KB2533623 or later): no Windows update needed"
        Return
    ${EndIf}
    ${Log} "AddDllDirectory is missing: installing KB2758857"
    ${If} $Rebooted == 1
    ${AndIf} $ForceKB != 1
        ; We already installed it and restarted, but it is still not active.
        ${Log} "Still missing after the restart"
        MessageBox MB_OK|MB_ICONSTOP "تحديث ويندوز اللي اتثبّت لسه مش شغّال حتى بعد إعادة التشغيل.$\r$\n$\r$\nشغّل Windows Update وثبّت كل التحديثات المهمة، وبعدين شغّل برنامج التثبيت ده تاني. (التفاصيل في $TEMP\HelperSetup.log)" /SD IDOK
        Abort
    ${EndIf}
    MessageBox MB_OKCANCEL|MB_ICONINFORMATION "قبل تثبيت المساعد لازم أثبّت تحديث صغير من مايكروسوفت للويندوز (KB2758857).$\r$\n$\r$\nبعد التحديث الكمبيوتر لازم يعيد التشغيل، وبعد ما يفتح تاني التثبيت هيكمّل لوحده.$\r$\n$\r$\nأكمّل؟" /SD IDOK IDOK +2
    Abort

    ${If} ${RunningX64}
        File "/oname=$PLUGINSDIR\kb2758857.msu" "payload\kb2758857-x64.msu"
    ${Else}
        File "/oname=$PLUGINSDIR\kb2758857.msu" "payload\kb2758857-x86.msu"
    ${EndIf}

    StrCpy $R1 0  ; tries
    retry:
    Banner::show /NOUNLOAD "بثبّت تحديث ويندوز... ممكن ياخد كام دقيقة، متقفلش الكمبيوتر."
    ClearErrors
    ${DisableX64FSRedirection}
!ifdef TEST_WUSA_CODE
    StrCpy $0 ${TEST_WUSA_CODE}  ; test builds only (makensis -DTEST_WUSA_CODE=3010): Wine cannot run wusa
!else
    ExecWait '"$SYSDIR\wusa.exe" "$PLUGINSDIR\kb2758857.msu" /quiet /norestart' $0
!endif
    ${EnableX64FSRedirection}
    Banner::destroy
    ${If} ${Errors}
        ${Log} "Could not start wusa.exe"
        MessageBox MB_OK|MB_ICONSTOP "مقدرتش أشغّل برنامج تحديثات ويندوز (wusa.exe). اطلب من أحمد يثبّت التحديث KB2758857 بنفسه." /SD IDOK
        Abort
    ${EndIf}
    IntFmt $1 "0x%08X" $0
    ${Log} "wusa.exe exit code $0 ($1)"

    ${If} $1 == "0x80070422"
    ${AndIf} $R1 == 0
        ; The Windows Update service is disabled; wusa needs it.  Allow it to start on demand, then retry.
        ${Log} "Windows Update service is disabled; setting it to manual start and retrying"
        nsExec::ExecToLog 'sc config wuauserv start= demand'
        Pop $2
        ${Log} "sc config wuauserv: $2"
        IntOp $R1 $R1 + 1
        Goto retry
    ${EndIf}

    ${If} $0 == 0
    ${OrIf} $0 == 3010
    ${OrIf} $1 == "0x00240006"
    ${OrIf} $1 == "0x00240005"
        Goto needs_restart
    ${EndIf}
    ${If} $1 == "0x80240017"
        MessageBox MB_OK|MB_ICONSTOP "تحديث ويندوز (KB2758857) مش مناسب للويندوز ده.$\r$\n$\r$\nشغّل Windows Update وثبّت كل التحديثات المهمة، وبعدين شغّل برنامج التثبيت ده تاني." /SD IDOK
        Abort
    ${EndIf}
    MessageBox MB_OK|MB_ICONSTOP "تثبيت تحديث ويندوز فشل (رقم الخطأ $1).$\r$\n$\r$\nأعد تشغيل الكمبيوتر وجرّب تاني. لو المشكلة فضلت، ابعت لأحمد الملف:$\r$\n$TEMP\HelperSetup.log" /SD IDOK
    Abort

    needs_restart:
    Call PrepareResume
    MessageBox MB_YESNO|MB_ICONQUESTION "تحديث ويندوز اتثبّت.$\r$\n$\r$\nلازم الكمبيوتر يعيد التشغيل دلوقتي. بعد ما يفتح، التثبيت هيكمّل لوحده (ولو مفتحش، دوس على أيقونة $\"${RESUME_LINK}$\" على سطح المكتب).$\r$\n$\r$\nأعيد تشغيل الكمبيوتر دلوقتي؟" /SD IDNO IDNO later
    ${Log} "Restarting the computer"
    FileClose $LogH
    Reboot
    later:
    MessageBox MB_OK|MB_ICONINFORMATION "تمام. أول ما تعيد تشغيل الكمبيوتر التثبيت هيكمّل." /SD IDOK
    ${Log} "Restart postponed by the user"
    Abort
FunctionEnd

; After the restart setup must start again WITH administrator rights.  Windows blocks start-up programs
; that need administrator rights, so a scheduled task "at log on, highest privileges" is used; a desktop
; icon is the fallback.
Function PrepareResume
    CreateDirectory "$LOCALAPPDATA\HelperSetup"
    CopyFiles /SILENT "$EXEPATH" "$LOCALAPPDATA\HelperSetup\HelperSetup.exe"
    ReadEnvStr $2 USERDOMAIN
    ReadEnvStr $3 USERNAME
    nsExec::ExecToLog 'schtasks /Create /F /TN "${RESUME_TASK}" /SC ONLOGON /RL HIGHEST /RU "$2\$3" /IT /TR "\"$LOCALAPPDATA\HelperSetup\HelperSetup.exe\" /AFTERREBOOT"'
    Pop $0
    ${Log} "schtasks /Create: $0"
    SetShellVarContext current
    CreateShortCut "$DESKTOP\${RESUME_LINK}.lnk" "$LOCALAPPDATA\HelperSetup\HelperSetup.exe" "/AFTERREBOOT" "$LOCALAPPDATA\HelperSetup\HelperSetup.exe" 0
FunctionEnd

Function CleanupResume
    nsExec::Exec 'schtasks /Delete /F /TN "${RESUME_TASK}"'
    Pop $0
    SetShellVarContext current
    Delete "$DESKTOP\${RESUME_LINK}.lnk"
    ${If} ${FileExists} "$LOCALAPPDATA\HelperSetup\HelperSetup.exe"
        Delete /REBOOTOK "$LOCALAPPDATA\HelperSetup\HelperSetup.exe"
        RMDir /REBOOTOK "$LOCALAPPDATA\HelperSetup"
    ${EndIf}
FunctionEnd

; ---------------------------------------------------------------- settings page

Function SettingsPage
    ${If} ${FileExists} "$INSTDIR\config.json"
        StrCpy $HadConfig 1
    ${Else}
        StrCpy $HadConfig 0
    ${EndIf}
    !insertmacro MUI_HEADER_TEXT "الإعدادات" "البيانات اللي المساعد محتاجها عشان يشتغل"
    nsDialogs::Create 1018
    Pop $Dlg
    ${If} $Dlg == error
        Abort
    ${EndIf}

    ${If} $HadConfig == 1
        ${NSD_CreateLabel} 0 0 100% 20u "المساعد متثبّت قبل كده والإعدادات القديمة محفوظة. سيب أي خانة فاضية عشان تفضل زي ما هي."
    ${Else}
        ${NSD_CreateLabel} 0 0 100% 20u "اكتب البيانات دي (أحمد عنده المفتاح وباسورد الإيميل). تقدر تغيّرها بعدين بتشغيل البرنامج ده تاني."
    ${EndIf}
    Pop $0

    ${NSD_CreateLabel} 62% 24u 38% 12u "اسم الوالد:"
    Pop $0
    ${NSD_CreateText} 0 22u 60% 12u $UserName
    Pop $hName

    ${NSD_CreateLabel} 62% 42u 38% 12u "مفتاح Claude API:"
    Pop $0
    ${NSD_CreateText} 0 40u 60% 12u $ApiKey
    Pop $hKey

    ${NSD_CreateLabel} 62% 60u 38% 12u "إيميل أحمد (للتقارير):"
    Pop $0
    ${NSD_CreateText} 0 58u 60% 12u $MailTo
    Pop $hTo

    ${NSD_CreateLabel} 62% 78u 38% 12u "إيميل Gmail اللي بيبعت:"
    Pop $0
    ${NSD_CreateText} 0 76u 60% 12u $SmtpUser
    Pop $hFrom

    ${NSD_CreateLabel} 62% 96u 38% 12u "App Password بتاع Gmail:"
    Pop $0
    ${NSD_CreatePassword} 0 94u 60% 12u $SmtpPass
    Pop $hPass

    ${NSD_CreateLabel} 0 114u 100% 28u "المفتاح من console.anthropic.com. الـ App Password من حساب جوجل: الأمان > التحقق بخطوتين > كلمات مرور التطبيقات. لو إيميل الإرسال فاضي هيتبعت من إيميل أحمد نفسه. الإيميل اختياري."
    Pop $0

    ${NSD_SetFocus} $hName
    nsDialogs::Show
FunctionEnd

Function SettingsLeave
    ${NSD_GetText} $hName $UserName
    ${NSD_GetText} $hKey $ApiKey
    ${NSD_GetText} $hTo $MailTo
    ${NSD_GetText} $hFrom $SmtpUser
    ${NSD_GetText} $hPass $SmtpPass

    ${If} $MailTo != ""
        ${StrStr} $0 $MailTo "@"
        ${If} $0 == ""
            MessageBox MB_OK|MB_ICONEXCLAMATION "إيميل أحمد شكله مش صحيح (لازم يكون فيه @)." /SD IDOK
            Abort
        ${EndIf}
    ${EndIf}
    ${If} $SmtpUser != ""
        ${StrStr} $0 $SmtpUser "@"
        ${If} $0 == ""
            MessageBox MB_OK|MB_ICONEXCLAMATION "إيميل الإرسال شكله مش صحيح (لازم يكون فيه @)." /SD IDOK
            Abort
        ${EndIf}
    ${EndIf}
    ${If} $HadConfig == 0
    ${AndIf} $ApiKey == ""
        MessageBox MB_YESNO|MB_ICONEXCLAMATION "من غير مفتاح Claude API المساعد مش هيقدر يرد على أي حاجة.$\r$\n$\r$\nتكمّل من غيره؟ (تقدر تضيفه بعدين بتشغيل برنامج التثبيت تاني)" /SD IDYES IDYES +2
        Abort
    ${EndIf}
FunctionEnd

; ---------------------------------------------------------------- install

Section "Main"
    SetShellVarContext current
    SetOutPath "$INSTDIR"
    ${Log} "Installing to $INSTDIR"
    ${If} ${FileExists} "$INSTDIR\config.json"
        StrCpy $HadConfig 1
    ${Else}
        StrCpy $HadConfig 0
    ${EndIf}
    Call CleanupResume
    Call EnsureAppClosed

    DetailPrint "بنسخ ملفات المساعد..."
    RMDir /r "$INSTDIR\helper\__pycache__"
    ClearErrors
    File "..\Start Assistant.pyw"
    File "..\install_helper.py"
    File "..\setup_assistant.py"
    File "..\SETUP.bat"
    File "..\config.example.json"
    File "..\README.md"
    File "helper.ico"
    SetOutPath "$INSTDIR\helper"
    File "..\helper\*.py"
    File "..\helper\cacert.pem"
    SetOutPath "$INSTDIR"
    ${If} ${Errors}
        ${Fail} "مقدرتش أنسخ ملفات المساعد في $INSTDIR. اتأكد إن في مساحة فاضية على الهارد وجرّب تاني."
    ${EndIf}
    ${Log} "App files copied"

    Call EnsurePython
    Call WriteSettings

    ${If} $HadConfig == 0
    ${AndIfNot} ${Silent}
        Call ArabicCheck
    ${EndIf}

    DetailPrint "بعمل أيقونة على سطح المكتب..."
    CreateShortCut "$DESKTOP\${APPNAME}.lnk" "$PyDir\pythonw.exe" '"$INSTDIR\Start Assistant.pyw"' "$INSTDIR\helper.ico" 0 SW_SHOWNORMAL "" "مساعدك على الكمبيوتر"
    CreateShortCut "$SMPROGRAMS\${APPNAME}.lnk" "$PyDir\pythonw.exe" '"$INSTDIR\Start Assistant.pyw"' "$INSTDIR\helper.ico" 0 SW_SHOWNORMAL "" "مساعدك على الكمبيوتر"
    ${IfNot} ${FileExists} "$DESKTOP\${APPNAME}.lnk"
        ${If} ${FileExists} "$SMPROGRAMS\${APPNAME}.lnk"
            ${Log} "Desktop shortcut could not be created (Start menu one is there)"
            MessageBox MB_OK|MB_ICONEXCLAMATION "مقدرتش أعمل أيقونة على سطح المكتب. المساعد موجود في قايمة ابدأ باسم $\"${APPNAME}$\"." /SD IDOK
        ${Else}
            ${Fail} "مقدرتش أعمل أيقونة للمساعد على سطح المكتب ولا في قايمة ابدأ."
        ${EndIf}
    ${EndIf}

    WriteUninstaller "$INSTDIR\uninstall.exe"
    WriteRegStr HKCU "${UNINSTKEY}" "DisplayName" "${APPNAME} (Helper)"
    WriteRegStr HKCU "${UNINSTKEY}" "DisplayVersion" "${VERSION}"
    WriteRegStr HKCU "${UNINSTKEY}" "Publisher" "Ahmed"
    WriteRegStr HKCU "${UNINSTKEY}" "DisplayIcon" "$INSTDIR\helper.ico"
    WriteRegStr HKCU "${UNINSTKEY}" "InstallLocation" "$INSTDIR"
    WriteRegStr HKCU "${UNINSTKEY}" "PythonDir" "$PyDir"
    WriteRegStr HKCU "${UNINSTKEY}" "UninstallString" '"$INSTDIR\uninstall.exe"'
    WriteRegDWORD HKCU "${UNINSTKEY}" "NoModify" 1
    WriteRegDWORD HKCU "${UNINSTKEY}" "NoRepair" 1
    ${Log} "Shortcuts and uninstaller created"

    ${If} $NoTest != 1
        Call TestEverything
    ${EndIf}
    ${Log} "Installation finished"
    Call SaveLog
SectionEnd

Function .onInstFailed
    Call SaveLog
FunctionEnd

Function .onGUIEnd
    ${If} $LogH != ""
        FileClose $LogH
        StrCpy $LogH ""
    ${EndIf}
FunctionEnd

; Ask the person to close the helper if it is running, so its files can be replaced.
Function EnsureAppClosed
    again:
    System::Call 'kernel32::OpenMutexW(i 0x00100000, i 0, w "Local\HomeAssistantWindow") p .r0'
    System::Call 'kernel32::OpenMutexW(i 0x00100000, i 0, w "Local\HomeAssistantLauncher") p .r1'
    ${If} $0 != 0
    ${OrIf} $1 != 0
        System::Call 'kernel32::CloseHandle(p r0)'
        System::Call 'kernel32::CloseHandle(p r1)'
        ${Log} "The helper is running; asking to close it"
        MessageBox MB_RETRYCANCEL|MB_ICONEXCLAMATION "المساعد مفتوح دلوقتي. اقفله (من علامة X فوق)، وبعدين دوس $\"إعادة المحاولة$\"." /SD IDCANCEL IDRETRY again
        ${Fail} "التثبيت اتلغى لأن المساعد كان مفتوح."
    ${EndIf}
FunctionEnd

; $PyDir = folder of a registered Python 3.8 32-bit that has python.exe and pythonw.exe, or "".
Function FindPython
    StrCpy $PyDir ""
    ReadRegStr $0 HKCU "${PYKEY}\InstallPath" ""
    ${If} $0 == ""
        ReadRegStr $0 HKLM "${PYKEY}\InstallPath" ""
    ${EndIf}
    ${If} $0 != ""
        StrCpy $1 $0 1 -1
        ${If} $1 == "\"
            StrCpy $0 $0 -1
        ${EndIf}
        ${If} ${FileExists} "$0\python.exe"
        ${AndIf} ${FileExists} "$0\pythonw.exe"
            StrCpy $PyDir $0
        ${EndIf}
    ${EndIf}
FunctionEnd

; $0 = 0 if Python starts and has ctypes, ssl and Tk.
Function CheckPython
    nsExec::ExecToLog '"$PyDir\python.exe" -E -s "$INSTDIR\install_helper.py" check-python'
    Pop $0
    ${Log} "Python check ($PyDir): $0"
FunctionEnd

Function RunPythonInstaller
    ; $R2 = arguments
    DetailPrint "بثبّت بايثون 3.8 (ممكن ياخد كام دقيقة)..."
    ${Log} "python-3.8.10.exe $R2"
    ClearErrors
    ExecWait '"$PLUGINSDIR\python-3.8.10.exe" $R2 /log "$TEMP\HelperSetup-python.log"' $0
    ${If} ${Errors}
        StrCpy $0 "could-not-start"
    ${EndIf}
    ${Log} "Python installer exit code: $0 (its own log: $TEMP\HelperSetup-python.log)"
FunctionEnd

Function EnsurePython
    DetailPrint "بدوّر على بايثون..."
    Call FindPython
    ${If} $PyDir != ""
        Call CheckPython
        ${If} $0 == 0
            ${Log} "Using the Python that is already installed: $PyDir"
            Return
        ${EndIf}
        ${Log} "The installed Python does not work; repairing it"
    ${Else}
        ${Log} "Python 3.8 32-bit not found; installing it"
    ${EndIf}

    File "/oname=$PLUGINSDIR\python-3.8.10.exe" "payload\python-3.8.10.exe"
    ${If} $PyDir != ""
        StrCpy $R2 "/repair /quiet"
        Call RunPythonInstaller
        Call FindPython
        ${If} $PyDir != ""
            Call CheckPython
            ${If} $0 == 0
                Return
            ${EndIf}
        ${EndIf}
    ${EndIf}

    StrCpy $R2 '/quiet ${PYARGS} TargetDir="$LOCALAPPDATA\Programs\Python\Python38-32"'
    Call RunPythonInstaller
    ${If} $0 == 1602
    ${OrIf} $0 == 1223
        ${Fail} "تثبيت بايثون اتلغى. شغّل برنامج التثبيت تاني ووافق على كل الأسئلة."
    ${EndIf}
    ${If} $0 == 1638
        ${Fail} "في نسخة تانية من بايثون 3.8 متثبّتة ومش شغّالة. امسحها من لوحة التحكم (إضافة أو إزالة البرامج) وشغّل برنامج التثبيت ده تاني."
    ${EndIf}
    ${If} $0 != 0
    ${AndIf} $0 != 3010
        ${Fail} "تثبيت بايثون فشل (رقم الخطأ $0). أعد تشغيل الكمبيوتر وجرّب تاني."
    ${EndIf}

    Call FindPython
    ${If} $PyDir == ""
        ${Fail} "بايثون اتثبّت بس مش لاقيه. أعد تشغيل الكمبيوتر وشغّل برنامج التثبيت ده تاني."
    ${EndIf}
    Call CheckPython
    ${If} $0 != 0
        ${Fail} "بايثون اتثبّت بس مش شغّال صح (رقم $0). أعد تشغيل الكمبيوتر وشغّل برنامج التثبيت ده تاني."
    ${EndIf}
    ${Log} "Python installed: $PyDir"
FunctionEnd

Function WriteSettings
    DetailPrint "بحفظ الإعدادات..."
    FileOpen $0 "$PLUGINSDIR\settings.txt" w
    ${If} $0 == ""
        ${Fail} "مقدرتش أحفظ الإعدادات (ملف مؤقت)."
    ${EndIf}
    FileWriteWord $0 0xFEFF
    FileWriteUTF16LE $0 "user_name=$UserName$\r$\n"
    FileWriteUTF16LE $0 "api_key=$ApiKey$\r$\n"
    FileWriteUTF16LE $0 "send_to=$MailTo$\r$\n"
    FileWriteUTF16LE $0 "smtp_username=$SmtpUser$\r$\n"
    FileWriteUTF16LE $0 "smtp_app_password=$SmtpPass$\r$\n"
    FileClose $0
    nsExec::ExecToLog '"$PyDir\python.exe" -E -s "$INSTDIR\install_helper.py" write-config "$PLUGINSDIR\settings.txt"'
    Pop $1
    Delete "$PLUGINSDIR\settings.txt"
    ${Log} "write-config: $1"
    ${If} $1 != 0
        ${Fail} "مقدرتش أحفظ الإعدادات في config.json (رقم $1)."
    ${EndIf}
FunctionEnd

Function ArabicCheck
    MessageBox MB_OK|MB_ICONINFORMATION "دلوقتي هيظهر شباك فيه نفس الجملة مكتوبة مرتين، جنب كل واحدة رقم (1 و 2).$\r$\n$\r$\nدوس على رقم الجملة اللي مكتوبة صح وتقدر تقراها بالترتيب الطبيعي من اليمين للشمال." /SD IDOK
    ExecWait '"$PyDir\pythonw.exe" -E -s "$INSTDIR\install_helper.py" arabic-check' $0
    ${Log} "arabic-check: $0"
FunctionEnd

Function TestEverything
    DetailPrint "باختبر الاتصال بالذكاء الاصطناعي والإيميل..."
    Delete "$PLUGINSDIR\result.txt"
    nsExec::ExecToLog '"$PyDir\python.exe" -E -s "$INSTDIR\install_helper.py" test "$PLUGINSDIR\result.txt"'
    Pop $0
    ${Log} "test: $0"
    StrCpy $R9 ""
    ClearErrors
    FileOpen $1 "$PLUGINSDIR\result.txt" r
    ${IfNot} ${Errors}
        read_loop:
        ClearErrors
        FileReadUTF16LE $1 $2
        ${IfNot} ${Errors}
            StrCpy $R9 "$R9$2"
            Goto read_loop
        ${EndIf}
        FileClose $1
    ${EndIf}
    ${If} $0 == 0
        MessageBox MB_OK|MB_ICONINFORMATION "كل حاجة شغّالة:$\r$\n$\r$\n$R9" /SD IDOK
    ${Else}
        MessageBox MB_OK|MB_ICONEXCLAMATION "التثبيت خلص، بس في حاجة محتاجة تتصلّح:$\r$\n$\r$\n$R9$\r$\nتقدر تصلّحها بعدين بتشغيل برنامج التثبيت ده تاني وكتابة البيانات الصح." /SD IDOK
    ${EndIf}
FunctionEnd

; Start the helper through Explorer, so it does not run with the installer's administrator rights.
Function LaunchApp
    Exec '"$WINDIR\explorer.exe" "$DESKTOP\${APPNAME}.lnk"'
FunctionEnd

; ---------------------------------------------------------------- uninstall

Function un.onInit
    SetShellVarContext current
FunctionEnd

Section "Uninstall"
    SetShellVarContext current
    again:
    System::Call 'kernel32::OpenMutexW(i 0x00100000, i 0, w "Local\HomeAssistantWindow") p .r0'
    ${If} $0 != 0
        System::Call 'kernel32::CloseHandle(p r0)'
        MessageBox MB_RETRYCANCEL|MB_ICONEXCLAMATION "المساعد مفتوح دلوقتي. اقفله وبعدين دوس $\"إعادة المحاولة$\"." /SD IDCANCEL IDRETRY again
        Abort
    ${EndIf}
    Delete "$DESKTOP\${APPNAME}.lnk"
    Delete "$SMPROGRAMS\${APPNAME}.lnk"
    RMDir /r "$INSTDIR\helper"
    Delete "$INSTDIR\Start Assistant.pyw"
    Delete "$INSTDIR\install_helper.py"
    Delete "$INSTDIR\setup_assistant.py"
    Delete "$INSTDIR\SETUP.bat"
    Delete "$INSTDIR\config.example.json"
    Delete "$INSTDIR\README.md"
    Delete "$INSTDIR\helper.ico"
    MessageBox MB_YESNO|MB_DEFBUTTON2|MB_ICONQUESTION "أمسح كمان إعدادات المساعد وسجلّاته؟$\r$\n$\r$\n(لو هتثبّته تاني اختار $\"لا$\" عشان الإعدادات تفضل محفوظة.)" /SD IDNO IDNO keep
    Delete "$INSTDIR\config.json"
    RMDir /r "$INSTDIR\data"
    keep:
    Delete "$INSTDIR\uninstall.exe"
    RMDir "$INSTDIR"
    DeleteRegKey HKCU "${UNINSTKEY}"
SectionEnd
