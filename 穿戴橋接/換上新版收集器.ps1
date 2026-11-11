# Default is read-only. Real use requires a separately authorized restart window.
[CmdletBinding()]
param([switch]$DryRun = $true, [switch]$Rollback, [switch]$TestLogic)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$Bridge = $PSScriptRoot
$Collector = Join-Path $Bridge 'hc_puller.py'
$Launcher = Join-Path $Bridge 'run-health-puller-hidden.vbs'
$Bat = Join-Path $Bridge '啟動收資料.bat'
$Backup = "$Collector.bak-20261004-夜"
$ExpectedBackupHash = 'e9fe88566579191b0d1d6c82f927d4dfc21730095c01e089a3294286704e9e1f'
$Log = Join-Path (Split-Path $Bridge -Parent) '生理資料\puller.log'
$Lock = Join-Path $Bridge '.hc-replace.lock'

function Find-HcProcess {
    param([object[]]$Processes, [string]$ScriptPath)
    $found = @()
    foreach ($proc in $Processes) {
        if ($proc.Name -ine 'pythonw.exe') { continue }
        if ([string]::IsNullOrWhiteSpace($proc.CommandLine)) {
            throw "Cannot inspect pythonw PID $($proc.ProcessId); refusing to guess."
        }
        # Accept only the verified launcher signature, with exact argument boundaries.
        $tokens = @([regex]::Matches($proc.CommandLine, '"[^"]*"|[^\s"]+') | ForEach-Object { $_.Value.Trim('"') })
        if (@($proc.CommandLine.ToCharArray() | Where-Object { $_ -eq '"' }).Count % 2 -ne 0) {
            throw 'Unbalanced command-line quotes; refusing to guess.'
        }
        if ($tokens.Count -lt 2) { continue }
        if ([IO.Path]::GetFileName($tokens[1]) -ine 'hc_puller.py') { continue }
        if (-not [IO.Path]::IsPathRooted($tokens[1])) { throw 'HC script path is relative; cannot verify ownership.' }
        if ([IO.Path]::GetFullPath($tokens[1]) -ine [IO.Path]::GetFullPath($ScriptPath)) { continue }
        if ($tokens.Count -ne 5 -or $tokens[2] -cne '--interval' -or $tokens[3] -cne '10' -or $tokens[4] -cne '--log') {
            throw 'HC arguments differ from the verified launcher; refusing to stop it.'
        }
        if ([IO.Path]::GetFileName($tokens[0]) -ine 'pythonw.exe') { throw 'Unexpected executable in HC command line.' }
        $found += $proc
    }
    return $found
}
function Get-UniqueHc {
    param([object[]]$Processes)
    $hits = @(Find-HcProcess -Processes $Processes -ScriptPath $Collector)
    if ($hits.Count -ne 1) { throw "Expected exactly one HC PID; found $($hits.Count). No action taken." }
    return $hits[0]
}
function Read-Processes {
    # Access denied or incomplete command lines must terminate before mutation.
    return @(Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" -ErrorAction Stop)
}
function Assert-LaunchChain {
    foreach ($path in @($Collector,$Launcher,$Bat)) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Required file missing: $path" }
    }
    $vbs = Get-Content -LiteralPath $Launcher -Raw -Encoding Unicode
    $batText = Get-Content -LiteralPath $Bat -Raw
    $expectedRun = 'shell.Run "cmd.exe /c ""' + $Bat + '""", 0, False'
    $expectedDir = 'shell.CurrentDirectory = "' + $Bridge + '"'
    $active = @($vbs -split '\r?\n' | ForEach-Object { $_.Trim().Trim([char]0xFEFF) } | Where-Object { $_ -and -not $_.StartsWith("'") })
    $expectedLines = @('Option Explicit','Dim shell','Set shell = CreateObject("WScript.Shell")',$expectedDir,$expectedRun)
    if (($active -join "\n") -cne ($expectedLines -join "\n")) { throw 'VBS content differs from verified launcher.' }
    $starts = @($batText -split '\r?\n' | Where-Object { $_ -match '^start\s' })
    if ($starts.Count -ne 1 -or $starts[0] -cne 'start "" /b "%PY%" "%~dp0hc_puller.py" --interval 10 --log') { throw 'BAT launch signature changed.' }
    $runtime = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\pythonw.exe'
    if (-not (Test-Path -LiteralPath $runtime -PathType Leaf)) { throw "Launcher runtime unavailable: $runtime" }
    $expectedPy = 'set "PY=%LOCALAPPDATA%\Programs\Python\Python313\pythonw.exe"'
    if (-not ($batText -split '\r?\n' -ccontains $expectedPy)) { throw 'BAT runtime differs from verified launcher.' }
    if ($Rollback) {
        if (-not (Test-Path -LiteralPath $Backup -PathType Leaf)) { throw 'Rollback backup missing.' }
        if ((Get-FileHash -LiteralPath $Backup -Algorithm SHA256).Hash -ine $ExpectedBackupHash) { throw 'Rollback backup hash mismatch.' }
    }
}
function Invoke-LogicTests {
    $fixture = [pscustomobject]@{Name='pythonw.exe';ProcessId=12345;CommandLine=('"C:\Python\pythonw.exe" "'+$Collector+'" --interval 10 --log');ExecutablePath='C:\Python\pythonw.exe'}
    foreach ($count in @(0,1,2)) {
        $items = @(); for ($i=0;$i -lt $count;$i++) { $items += [pscustomobject]@{Name=$fixture.Name;ProcessId=(12345+$i);CommandLine=$fixture.CommandLine;ExecutablePath=$fixture.ExecutablePath} }
        $accepted = $false
        try { $null = Get-UniqueHc -Processes $items; $accepted = $true } catch { if ($count -eq 1) { throw } }
        if ($accepted -ne ($count -eq 1)) { throw "FAIL count=$count" }
        Write-Output "PASS fake PID count=$count accepted=$accepted"
    }
    foreach ($command in @($null, 'pythonw.exe hc_puller.py --interval 10 --log', ('pythonw.exe "'+$Collector+'" --interval 10 --logger'))) {
        $bad = [pscustomobject]@{Name='pythonw.exe';ProcessId=999;CommandLine=$command}
        $blocked = $false
        try { $null = Get-UniqueHc -Processes @($bad) } catch { $blocked = $true }
        if (-not $blocked) { throw 'FAIL unsafe signature accepted' }
        Write-Output 'PASS unsafe/unknown signature blocked'
    }
    Write-Output 'PASS TestLogic: no real processes queried, stopped or started.'
}

if ($TestLogic) {
    if (-not $DryRun) { throw 'TestLogic requires DryRun.' }
    Invoke-LogicTests
    return
}
$lockStream = $null
$stopped = $false
try {
    Write-Output ('Mode: ' + $(if ($DryRun) { 'DryRun (no mutation)' } else { 'LIVE (explicit -DryRun:$false)' }))
    $target = Get-UniqueHc -Processes @(Read-Processes)
    Assert-LaunchChain
    Write-Output "Verified unique HC PID: $($target.ProcessId)"
    if ($DryRun) {
        Write-Output "Would stop ONLY PID $($target.ProcessId) after rechecking its identity."
        if ($Rollback) { Write-Output "Would preserve current hc_puller.py, then restore verified backup $Backup (backup unchanged)." }
        Write-Output "Would start ONCE: wscript.exe //B //NoLogo $Launcher"
        Write-Output "Would verify one NEW HC PID plus a NEW startup marker in $Log within 60 seconds."
        Write-Output 'DryRun complete. No stop, start, copy, lock or log write occurred.'
        return
    }
    # Serialize this button only; do not claim to guard independent Startup triggers.
    $lockStream = [IO.File]::Open($Lock,[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)
    $again = Get-UniqueHc -Processes @(Read-Processes)
    if ($again.ProcessId -ne $target.ProcessId -or $again.CommandLine -cne $target.CommandLine) { throw 'PID/command changed; refusing to stop.' }
    Assert-LaunchChain
    $beforeText = if (Test-Path -LiteralPath $Log) { [IO.File]::ReadAllText($Log,[Text.Encoding]::UTF8) } else { '' }
    $beforeMarkers = @([regex]::Matches($beforeText,'(?m)^===== 啟動 ([^\r\n]+) =====\r?$') | ForEach-Object {$_.Value})
    $startedAt = [DateTimeOffset]::Now
    # Capture a non-overwriting safety copy before stopping, so backup failure leaves HC running.
    if ($Rollback) {
        $save = "$Collector.bak-before-rollback-$([DateTime]::Now.ToString('yyyyMMdd-HHmmss-fffffff'))"
        [IO.File]::Copy($Collector,$save,$false)
        Write-Output "Preserved current collector: $save"
    }
    Stop-Process -Id $target.ProcessId -ErrorAction Stop
    $stopped = $true
    $deadline = [DateTime]::UtcNow.AddSeconds(10)
    do {
        if (-not (Get-Process -Id $target.ProcessId -ErrorAction SilentlyContinue)) { break }
        if ([DateTime]::UtcNow -ge $deadline) { throw 'Old PID did not exit; launcher not run.' }
        Start-Sleep -Milliseconds 250
    } while ($true)
    if (@(Find-HcProcess -Processes @(Read-Processes) -ScriptPath $Collector).Count -ne 0) { throw 'Another HC already exists; launcher not run.' }
    if ($Rollback) { [IO.File]::Copy($Backup,$Collector,$true) }
    $null = Start-Process -FilePath "$env:WINDIR\System32\wscript.exe" -ArgumentList @('//B','//NoLogo', ('"'+$Launcher+'"')) -WindowStyle Hidden -PassThru -ErrorAction Stop
    $deadline = [DateTime]::UtcNow.AddSeconds(60)
    do {
        $hits = @(Find-HcProcess -Processes @(Read-Processes) -ScriptPath $Collector)
        if ($hits.Count -gt 1) { throw 'Duplicate HC detected. No automatic retry or further killing.' }
        $fresh = $false
        if (Test-Path -LiteralPath $Log) {
            $logText = [IO.File]::ReadAllText($Log,[Text.Encoding]::UTF8)
            foreach ($match in [regex]::Matches($logText,'(?m)^===== 啟動 ([^\r\n]+) =====\r?$')) {
                $time = [DateTimeOffset]::MinValue
                if ($match.Value -notin $beforeMarkers -and [DateTimeOffset]::TryParse($match.Groups[1].Value,[ref]$time) -and $time -ge $startedAt) { $fresh = $true }
            }
        }
        if ($hits.Count -eq 1 -and $hits[0].ProcessId -ne $target.ProcessId -and $fresh) {
            Start-Sleep -Seconds 2
            $confirmed = Get-UniqueHc -Processes @(Read-Processes)
            if ($confirmed.ProcessId -ne $hits[0].ProcessId) { throw 'HC changed during confirmation.' }
            Write-Output "PASS: one new HC PID $($confirmed.ProcessId); new startup marker confirmed. Transport success still needs the normal collection check."
            return
        }
        if ([DateTime]::UtcNow -ge $deadline) { throw 'Startup verification timed out; no automatic retry.' }
        Start-Sleep -Milliseconds 500
    } while ($true)
} catch {
    Write-Output "STOP: $($_.Exception.Message)"
    if ($stopped) { Write-Output 'Old HC was stopped. Inspect this failure in an authorized window; do not blindly rerun or kill all Python.' }
    throw
} finally {
    if ($null -ne $lockStream) { $lockStream.Dispose() }
}
