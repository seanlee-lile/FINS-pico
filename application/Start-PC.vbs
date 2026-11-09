Option Explicit
Dim fileSystem, shell, projectRoot, pythonPath, windowPath, launchResult
Set fileSystem = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
projectRoot = fileSystem.GetParentFolderName(WScript.ScriptFullName)
windowPath = fileSystem.BuildPath(projectRoot, "communication\pc\pc_control.pyw")
pythonPath = "F:\py3.10\pythonw.exe"
If Not fileSystem.FileExists(pythonPath) Then pythonPath = "pythonw.exe"
shell.CurrentDirectory = projectRoot
On Error Resume Next
launchResult = shell.Run(Chr(34) & pythonPath & Chr(34) & " " & Chr(34) & windowPath & Chr(34), 1, False)
If Err.Number <> 0 Then
    MsgBox "Unable to start Python. Install Python with tkinter, then open communication\pc\pc_control.pyw.", 16, "IWIN PC"
End If
