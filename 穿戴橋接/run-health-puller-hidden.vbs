Option Explicit
' Hidden launcher created 2026-08-28 to stop the black box flashing at logon.
' Original shortcut backed up in: Desktop\阿寶遊樂園\黑框閃爍修理\
' Rollback: copy the .原始備份-20260828.lnk back into the Startup folder.
Dim shell
Set shell = CreateObject("WScript.Shell")
shell.CurrentDirectory = "C:\Users\<you>\Desktop\<repo>\穿戴橋接"
shell.Run "cmd.exe /c ""C:\Users\<you>\Desktop\<repo>\穿戴橋接\啟動收資料.bat""", 0, False
