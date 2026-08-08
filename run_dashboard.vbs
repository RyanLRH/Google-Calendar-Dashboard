' Launches the dashboard with zero flashing windows.
' Point Task Scheduler at this file (wscript.exe run_dashboard.vbs).
Set fso = CreateObject("Scripting.FileSystemObject")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = folder
sh.Run "pythonw.exe """ & folder & "\dashboard.pyw""", 0, False