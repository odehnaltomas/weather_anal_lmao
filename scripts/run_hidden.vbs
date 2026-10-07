Option Explicit

' Windows Script Host has no console window. Launching PowerShell through it
' prevents the brief console flash that can steal focus from full-screen apps.
Dim arguments
Set arguments = WScript.Arguments

If arguments.Count < 3 Then
    WScript.Quit 87
End If

Function QuoteArgument(value)
    QuoteArgument = Chr(34) & Replace(value, Chr(34), Chr(34) & Chr(34)) & Chr(34)
End Function

Dim command
command = QuoteArgument(arguments(0)) _
    & " -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass" _
    & " -File " & QuoteArgument(arguments(1))

If arguments.Count >= 4 Then
    ' VBScript evaluates both sides of And, even when the first is False.
    ' Maintenance supplies only three arguments, with no optional job name.
    If Len(arguments(3)) > 0 Then
        command = command & " -Job " & QuoteArgument(arguments(3))
    End If
End If

command = command & " -PythonExe " & QuoteArgument(arguments(2))

Dim shell
Set shell = CreateObject("WScript.Shell")
Dim exitCode
' Limit error suppression to process launch and turn launch errors into a
' failure code. Returning the child result enables Task Scheduler retries.
On Error Resume Next
exitCode = shell.Run(command, 0, True)
If Err.Number <> 0 Then
    WScript.Quit 1
End If
On Error GoTo 0
WScript.Quit exitCode
