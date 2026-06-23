param(
    [ValidateSet("setup", "start", "once", "stop", "status", "logs", "update", "help")]
    [string]$Action = "help"
)

$ErrorActionPreference = "Stop"
$InstallDir = Join-Path $env:LOCALAPPDATA "PokemonProductMonitor"
$VenvDir = Join-Path $InstallDir ".venv-xoplay"
$PythonExe = Join-Path $VenvDir "Scripts\python.exe"
$MonitorFile = Join-Path $InstallDir "xoplay_local_monitor.py"
$RequirementsFile = Join-Path $InstallDir "requirements-xoplay.txt"
$EnvFile = Join-Path $InstallDir ".env.xoplay"
$PidFile = Join-Path $InstallDir ".xoplay-monitor.pid"
$LogFile = Join-Path $InstallDir "xoplay-monitor.log"
$ErrorLogFile = Join-Path $InstallDir "xoplay-monitor-error.log"
$RawBase = "https://raw.githubusercontent.com/Jasa-S/pokemon-product-monitor/main"

function Write-Usage {
    Write-Host "Xoplay / Naver monitor for Windows"
    Write-Host ""
    Write-Host "  .\xoplay-monitor-windows.ps1 setup   One-time installation"
    Write-Host "  .\xoplay-monitor-windows.ps1 start   Run in the background"
    Write-Host "  .\xoplay-monitor-windows.ps1 once    Run one visible scan"
    Write-Host "  .\xoplay-monitor-windows.ps1 stop    Stop monitor and browser"
    Write-Host "  .\xoplay-monitor-windows.ps1 status  Show current state"
    Write-Host "  .\xoplay-monitor-windows.ps1 logs    Show recent logs"
    Write-Host "  .\xoplay-monitor-windows.ps1 update  Download the latest monitor"
}

function Get-MonitorProcess {
    if (-not (Test-Path $PidFile)) { return $null }
    $MonitorPid = (Get-Content $PidFile -Raw).Trim()
    if ($MonitorPid -notmatch '^\d+$') { return $null }
    return Get-Process -Id ([int]$MonitorPid) -ErrorAction SilentlyContinue
}

function Save-CurrentMonitor {
    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    Invoke-WebRequest -UseBasicParsing "$RawBase/xoplay_local_monitor.py" -OutFile $MonitorFile
    Invoke-WebRequest -UseBasicParsing "$RawBase/requirements-xoplay.txt" -OutFile $RequirementsFile
    Write-Host "Downloaded the latest monitor files."
}

function Import-MonitorEnvironment {
    if (-not (Test-Path $EnvFile)) { return }
    foreach ($Line in Get-Content $EnvFile) {
        $Value = $Line.Trim()
        if (-not $Value -or $Value.StartsWith("#") -or -not $Value.Contains("=")) { continue }
        $Parts = $Value.Split("=", 2)
        [Environment]::SetEnvironmentVariable($Parts[0].Trim(), $Parts[1].Trim(), "Process")
    }
}

function Assert-Ready {
    if (-not (Test-Path $PythonExe) -or -not (Test-Path $MonitorFile)) {
        throw "Run setup first: .\xoplay-monitor-windows.ps1 setup"
    }
    if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
        throw "GitHub CLI is required. Install it with: winget install --id GitHub.cli"
    }
    & gh auth status 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw "Sign in first with: gh auth login --web --git-protocol https"
    }
}

switch ($Action) {
    "setup" {
        if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
            throw "Install GitHub CLI first: winget install --id GitHub.cli"
        }
        Save-CurrentMonitor
        if (-not (Test-Path $PythonExe)) {
            if (Get-Command py -ErrorAction SilentlyContinue) {
                & py -3 -m venv $VenvDir
            } elseif (Get-Command python -ErrorAction SilentlyContinue) {
                & python -m venv $VenvDir
            } else {
                throw "Python 3 is required. Install it with: winget install --id Python.Python.3.13"
            }
        }
        & $PythonExe -m pip install --upgrade pip
        & $PythonExe -m pip install -r $RequirementsFile
        & $PythonExe -m playwright install webkit
        if (-not (Test-Path $EnvFile)) {
            @"
# Five minutes is deliberately conservative.
XOPLAY_POLL_SECONDS=300
XOPLAY_MAX_PAGES=20
XOPLAY_BROWSER=webkit
XOPLAY_HEADLESS=false
XOPLAY_GITHUB_SYNC=true
GITHUB_REPOSITORY=Jasa-S/pokemon-product-monitor
PYTHONUNBUFFERED=1
"@ | Set-Content -Encoding UTF8 $EnvFile
        }
        & gh auth status
        if ($LASTEXITCODE -ne 0) {
            Write-Host "Run: gh auth login --web --git-protocol https"
            throw "GitHub sign-in is required for dashboard updates and Discord alerts."
        }
        Write-Host "Setup complete. Stop the Mac monitor, then run:"
        Write-Host ".\xoplay-monitor-windows.ps1 start"
    }
    "update" {
        Save-CurrentMonitor
        if (Test-Path $PythonExe) { & $PythonExe -m pip install -r $RequirementsFile }
    }
    "start" {
        Assert-Ready
        $Existing = Get-MonitorProcess
        if ($Existing) {
            Write-Host "Monitor is already running (PID $($Existing.Id))."
            break
        }
        Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
        Import-MonitorEnvironment
        $Process = Start-Process -FilePath $PythonExe -ArgumentList @("`"$MonitorFile`"") `
            -WorkingDirectory $InstallDir -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput $LogFile -RedirectStandardError $ErrorLogFile
        Set-Content -Encoding ASCII $PidFile $Process.Id
        Write-Host "Monitor started (PID $($Process.Id))."
        Write-Host "A WebKit window will open. Complete Naver login or verification yourself if requested."
        Write-Host "Important: leave the Mac monitor stopped while this Windows monitor is running."
    }
    "once" {
        Assert-Ready
        if (Get-MonitorProcess) { throw "Stop the background monitor before running a one-time scan." }
        Import-MonitorEnvironment
        [Environment]::SetEnvironmentVariable("XOPLAY_RUN_ONCE", "true", "Process")
        & $PythonExe $MonitorFile
    }
    "stop" {
        $Existing = Get-MonitorProcess
        if ($Existing) {
            & taskkill.exe /PID $Existing.Id /T /F | Out-Null
            Write-Host "Monitor and its browser were stopped."
        } else {
            Write-Host "Monitor is not running."
        }
        Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    }
    "status" {
        $Existing = Get-MonitorProcess
        if ($Existing) { Write-Host "Monitor is running (PID $($Existing.Id))." }
        else { Write-Host "Monitor is stopped." }
    }
    "logs" {
        if (Test-Path $LogFile) { Get-Content $LogFile -Tail 80 }
        if (Test-Path $ErrorLogFile) { Get-Content $ErrorLogFile -Tail 80 }
    }
    "help" { Write-Usage }
}
