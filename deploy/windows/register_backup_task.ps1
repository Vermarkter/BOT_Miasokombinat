param(
    [string]$TaskName = "MeatbotDailyBackup",
    [string]$ProjectRoot = "",
    [string]$PythonExe = "",
    [string]$RunAt = "02:00"
)

$ErrorActionPreference = "Stop"

if (-not $ProjectRoot) {
    $ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
}

if (-not (Test-Path $ProjectRoot)) {
    throw "Project root not found: $ProjectRoot"
}

if (-not $PythonExe) {
    $venvPython = Join-Path $ProjectRoot "venv\Scripts\python.exe"
    if (Test-Path $venvPython) {
        $PythonExe = $venvPython
    } else {
        $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
        if (-not $pythonCmd) {
            throw "Python not found. Install Python or create venv in $ProjectRoot\venv"
        }
        $PythonExe = $pythonCmd.Source
    }
}

if (-not (Test-Path $PythonExe)) {
    throw "Python executable not found: $PythonExe"
}

$backupScript = Join-Path $ProjectRoot "scripts\backup_db.py"
if (-not (Test-Path $backupScript)) {
    throw "Backup script not found: $backupScript"
}

$timeSpan = [TimeSpan]::ParseExact($RunAt, "hh\:mm", $null)
$runDateTime = [datetime]::Today.Add($timeSpan)

$action = New-ScheduledTaskAction -Execute $PythonExe -Argument "scripts\backup_db.py" -WorkingDirectory $ProjectRoot
$trigger = New-ScheduledTaskTrigger -Daily -At $runDateTime
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -RunLevel Highest `
    -User "SYSTEM" `
    -Force | Out-Null

Write-Host "Scheduled task '$TaskName' registered (daily at $RunAt)."
Get-ScheduledTask -TaskName $TaskName | Format-List TaskName, State
