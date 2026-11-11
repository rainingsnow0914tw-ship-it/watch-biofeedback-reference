@echo off
rem ---------------------------------------------------------------
rem  Start the health-data collector in the background.
rem  Runs at logon via Startup shortcut; can also be double-clicked.
rem  No window is normal - it collects data silently.
rem
rem  PULL, not push: the phone app refuses plaintext HTTP outbound
rem  (Android network security policy), so the PC fetches from the
rem  app's own local HTTP server on the phone instead. Verified.
rem
rem  ASCII + CRLF on purpose - cmd.exe mis-parses UTF-8 / LF .bat.
rem
rem    status : python hc_baseline.py
rem    log    : puller.log in the data folder
rem    stop   : end pythonw.exe in Task Manager
rem
rem  Requires on the phone: HC Webhook -> Local HTTP Server ON (8787)
rem ---------------------------------------------------------------

set "PY=%LOCALAPPDATA%\Programs\Python\Python313\pythonw.exe"

start "" /b "%PY%" "%~dp0hc_puller.py" --interval 10 --log
