Option Explicit
Dim shell
Set shell = CreateObject("WScript.Shell")
shell.CurrentDirectory = "C:\Users\<you>\Desktop\<repo>\穿戴橋接"
shell.Run "cmd.exe /c ""C:\Users\<you>\Desktop\<repo>\穿戴橋接\啟動收資料.bat""", 0, False
