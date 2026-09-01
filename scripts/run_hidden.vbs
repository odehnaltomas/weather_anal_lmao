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

If arguments.Count >= 4 And Len(arguments(3)) > 0 Then
    command = command & " -Job " & QuoteArgument(arguments(3))
End If

command = command & " -PythonExe " & QuoteArgument(arguments(2))

Dim shell
Set shell = CreateObject("WScript.Shell")
WScript.Quit shell.Run(command, 0, True)
