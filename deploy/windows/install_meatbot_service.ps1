param(
    [string]$ServiceName = "meatbot",
    [string]$ProjectRoot = "",
    [string]$PythonExe = "",
    [string]$NssmExe = "nssm.exe"
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

$nssmPath = $null
if (Test-Path $NssmExe) {
    $nssmPath = (Resolve-Path $NssmExe).Path
} else {
    $nssmCmd = Get-Command $NssmExe -ErrorAction SilentlyContinue
    if ($nssmCmd) {
        $nssmPath = $nssmCmd.Source
    }
}

if (-not $nssmPath) {
    throw "NSSM not found. Download it from https://nssm.cc/download or add nssm.exe to PATH."
}

$logsDir = Join-Path $ProjectRoot "logs"
New-Item -ItemType Directory -Force -Path $logsDir | Out-Null

$existingService = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($existingService) {
    Write-Host "Service '$ServiceName' exists. Updating configuration..."
    if ($existingService.Status -eq "Running") {
        Stop-Service -Name $ServiceName -Force
    }
} else {
    Write-Host "Installing service '$ServiceName'..."
    & $nssmPath install $ServiceName $PythonExe "main.py" | Out-Null
}

& $nssmPath set $ServiceName AppDirectory $ProjectRoot | Out-Null
& $nssmPath set $ServiceName AppParameters "main.py" | Out-Null
& $nssmPath set $ServiceName DisplayName "Meatbot Telegram Service" | Out-Null
& $nssmPath set $ServiceName Description "Telegram bot for meat plant sales agents" | Out-Null
& $nssmPath set $ServiceName Start SERVICE_AUTO_START | Out-Null
& $nssmPath set $ServiceName AppExit Default Restart | Out-Null
& $nssmPath set $ServiceName AppEnvironmentExtra "PYTHONUNBUFFERED=1" | Out-Null
& $nssmPath set $ServiceName AppStdout (Join-Path $logsDir "service_stdout.log") | Out-Null
& $nssmPath set $ServiceName AppStderr (Join-Path $logsDir "service_stderr.log") | Out-Null
& $nssmPath set $ServiceName AppRotateFiles 1 | Out-Null
& $nssmPath set $ServiceName AppRotateOnline 1 | Out-Null

Start-Service -Name $ServiceName

Write-Host "Service '$ServiceName' is configured and started."
Get-Service -Name $ServiceName | Format-Table Name, Status, StartType -AutoSize
