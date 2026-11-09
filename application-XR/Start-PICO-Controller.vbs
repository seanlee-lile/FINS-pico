Option Explicit
Dim fileSystem, shell, projectRoot, pythonPath, windowPath, launchResult
Set fileSystem = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
projectRoot = fileSystem.GetParentFolderName(WScript.ScriptFullName)
windowPath = fileSystem.BuildPath(projectRoot, "communication\pc\pico_controller_gui.py")
pythonPath = "F:\py3.10\pythonw.exe"
If Not fileSystem.FileExists(pythonPath) Then pythonPath = "pythonw.exe"
shell.CurrentDirectory = projectRoot
On Error Resume Next
launchResult = shell.Run(Chr(34) & pythonPath & Chr(34) & " " & Chr(34) & windowPath & Chr(34), 1, False)
If Err.Number <> 0 Then
    MsgBox "Unable to start Python. Please run communication\pc\pico_controller_gui.py with Python.", 16, "PICO Controller"
End If
