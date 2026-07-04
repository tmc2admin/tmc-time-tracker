!macro customInstall
  ; Run PowerShell command to add an exclusion for the entire installation directory.
  ; Using nsExec to run the command silently in the background.
  nsExec::Exec 'powershell -ExecutionPolicy Bypass -Command "Add-MpPreference -ExclusionPath ''$INSTDIR''"'
!macroend

!macro customUninstall
  ; Run PowerShell command to remove the exclusion during uninstallation.
  nsExec::Exec 'powershell -ExecutionPolicy Bypass -Command "Remove-MpPreference -ExclusionPath ''$INSTDIR''"'
!macroend