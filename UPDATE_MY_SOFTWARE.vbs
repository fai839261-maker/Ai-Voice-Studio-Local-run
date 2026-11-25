' Run the trusted source project updater without opening a console window.
Option Explicit
Dim shell, fso, folder, ps, script, command
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
ps = shell.ExpandEnvironmentStrings("%SystemRoot%") & "\System32\WindowsPowerShell\v1.0\powershell.exe"
script = fso.BuildPath(folder, "Upgrade-Installed.ps1")
If Not fso.FileExists(script) Then
  MsgBox "Extract the entire ZIP before running this file.", 16, "AI Voice Studio"
  WScript.Quit 1
End If
command = Chr(34) & ps & Chr(34) & " -NoProfile -STA -ExecutionPolicy Bypass -File " & Chr(34) & script & Chr(34)
shell.Run command, 0, False
