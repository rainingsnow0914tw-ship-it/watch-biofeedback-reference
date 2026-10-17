@echo off
rem ---------------------------------------------------------------
rem  Shadow mode for the watch-bridge judgement layer.
rem
rem  Pulls raw watch data from the phone (port 8788) every 10 min,
rem  feeds it to triage.assess(), logs the verdict. DOES NOT NOTIFY.
rem
rem  Why no --notify: 使用者's rule is that any new person gets two
rem  weeks of observe-only first, using human care as the control.
rem  Do NOT add --notify before that period is up.
rem
rem  Positive control: runs canary.py every 6 hours and logs the
rem  result. Without it, "zero false alarms" and "the system never
rem  ran" are mathematically indistinguishable - this project has
rem  already been bitten by exactly that once.
rem
rem  ASCII + CRLF on purpose - cmd.exe mis-parses UTF-8 / LF .bat.
rem
rem    log  : ????\????\????.jsonl
rem    stop : end pythonw.exe in Task Manager
rem ---------------------------------------------------------------

set "PY=%LOCALAPPDATA%\Programs\Python\Python313\pythonw.exe"

start "" /b "%PY%" "%~dp0watch_puller.py" --interval 10

rem  2026-10-17: sense sentinel - every 10 min looks for 3 events (heart, skin, back)
rem  and lets Abao speak via phone notify. Remove this line to disable.
start "" /b "%PY%" "%~dp0sentinel.py" --loop
