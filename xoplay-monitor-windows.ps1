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
$StateFile = Join-Path $InstallDir ".naver-local-monitor-state.json"
$RawBase = "https://raw.githubusercontent.com/Jasa-S/pokemon-product-monitor/main"
$GithubRepository = "Jasa-S/pokemon-product-monitor"

# How long to wait between scan cycles (seconds)
$WaitBetweenScans = 420  # 7 minutes

function Write-Usage {
    Write-Host "Xoplay / Naver monitor for Windows"
    Write-Host ""
    Write-Host "  .\xoplay-monitor-windows.ps1 setup   One-time installation"
    Write-Host "  .\xoplay-monitor-windows.ps1 start   Run continuously (scan, wait 7 min, repeat)"
    Write-Host "  .\xoplay-monitor-windows.ps1 once    Run one visible scan"
    Write-Host "  .\xoplay-monitor-windows.ps1 stop    Stop the monitor loop"
    Write-Host "  .\xoplay-monitor-windows.ps1 status  Show current state"
    Write-Host "  .\xoplay-monitor-windows.ps1 logs    Show recent logs"
    Write-Host "  .\xoplay-monitor-windows.ps1 update  Download the latest monitor"
}

function Get-MonitorProcess {
    if (-not (Test-Path $PidFile)) { return $null }
    $StoredPid = (Get-Content $PidFile -Raw).Trim()
    if ($StoredPid -notmatch '^\d+$') { return $null }
    # The PID belongs to a PowerShell process (the loop), not Python
    return Get-Process -Id ([int]$StoredPid) -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match 'powershell|pwsh' }
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

function Set-WindowsBrowserConfig {
    if (-not (Test-Path $EnvFile)) { return }
    $Lines = @(Get-Content $EnvFile)
    $Found = @($Lines | Where-Object { $_ -match '^\s*XOPLAY_BROWSER=' }).Count -gt 0
    $Lines = $Lines | ForEach-Object {
        if ($_ -match '^\s*XOPLAY_BROWSER=') { "XOPLAY_BROWSER=chromium" } else { $_ }
    }
    if (-not $Found) { $Lines += "XOPLAY_BROWSER=chromium" }
    $Lines | Set-Content -Encoding UTF8 $EnvFile
}

function Sync-DiscordWebhook {
    if (-not (Test-Path $EnvFile)) { return }
    $Lines = @(Get-Content $EnvFile)
    $AlreadySet = @($Lines | Where-Object { $_ -match '^\s*DISCORD_WEBHOOK_URL=.+' }).Count -gt 0
    if ($AlreadySet) {
        Write-Host "DISCORD_WEBHOOK_URL is already set in .env.xoplay."
        return
    }
    Write-Host ""
    Write-Host "DISCORD_WEBHOOK_URL is not set. The monitor needs it to send CAPTCHA alerts."
    Write-Host "Find it in: Discord > Server Settings > Integrations > Webhooks"
    $WebhookUrl = Read-Host "Paste your Discord webhook URL"
    $WebhookUrl = $WebhookUrl.Trim()
    if ($WebhookUrl -match '^https://discord(app)?\.com/api/webhooks/') {
        $Lines += "DISCORD_WEBHOOK_URL=$WebhookUrl"
        $Lines | Set-Content -Encoding UTF8 $EnvFile
        Write-Host "DISCORD_WEBHOOK_URL saved to .env.xoplay."
    } else {
        Write-Host "Invalid or empty URL; skipping. Add it manually to: $EnvFile"
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

function Show-Countdown {
    param([int]$Seconds)
    $Deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $Deadline) {
        $Remaining = [int](($Deadline - (Get-Date)).TotalSeconds)
        $Mins = [int]($Remaining / 60)
        $Secs = $Remaining % 60
        Write-Host -NoNewline "`r  Next scan in: $($Mins.ToString('D2')):$($Secs.ToString('D2'))  "
        Start-Sleep -Seconds 1
    }
    Write-Host "`r                              "  # clear the countdown line
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
        & $PythonExe -m playwright install chromium
        if (-not (Test-Path $EnvFile)) {
            @"
XOPLAY_MAX_PAGES=20
XOPLAY_BROWSER=chromium
XOPLAY_HEADLESS=false
XOPLAY_GITHUB_SYNC=true
GITHUB_REPOSITORY=Jasa-S/pokemon-product-monitor
PYTHONUNBUFFERED=1
"@ | Set-Content -Encoding UTF8 $EnvFile
        }
        Set-WindowsBrowserConfig
        Sync-DiscordWebhook
        & gh auth status
        if ($LASTEXITCODE -ne 0) {
            Write-Host "Run: gh auth login --web --git-protocol https"
            throw "GitHub sign-in is required for dashboard updates and Discord alerts."
        }
        Write-Host "Setup complete. Run: .\xoplay-monitor-windows.ps1 start"
    }
    "update" {
        Save-CurrentMonitor
        if (Test-Path $PythonExe) {
            & $PythonExe -m pip install -r $RequirementsFile
            & $PythonExe -m playwright install chromium
        }
        Set-WindowsBrowserConfig
        Sync-DiscordWebhook
        Write-Host "Updated. Run: .\xoplay-monitor-windows.ps1 start"
    }
    "start" {
        Assert-Ready
        if (Get-MonitorProcess) {
            Write-Host "Monitor is already running (PID $((Get-MonitorProcess).Id))."
            break
        }

        # Store this PowerShell loop's own PID so 'stop' and 'status' can find it.
        Set-Content -Encoding ASCII $PidFile $PID
        Write-Host "Monitor started (PID $PID). Press Ctrl+C or run 'stop' in another window to quit."
        Write-Host "Chromium will open for each scan, then close automatically when done."

        Import-MonitorEnvironment

        try {
            $ScanNumber = 0
            while ($true) {
                $ScanNumber++
                $Timestamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
                Write-Host ""
                Write-Host "=== Scan #$ScanNumber started at $Timestamp ==="

                # Run one full scan. Python exits when the scan is complete.
                & $PythonExe $MonitorFile *>> $LogFile

                $FinishTime = Get-Date -Format 'HH:mm:ss'
                Write-Host "=== Scan #$ScanNumber finished at $FinishTime. Next in $([int]($WaitBetweenScans/60)) min ==="

                # Live countdown in the terminal
                Show-Countdown -Seconds $WaitBetweenScans
            }
        } finally {
            Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
        }
    }
    "once" {
        Assert-Ready
        if (Get-MonitorProcess) { throw "Stop the background monitor before running a one-time scan." }
        Import-MonitorEnvironment
        & $PythonExe $MonitorFile
    }
    "stop" {
        $Existing = Get-MonitorProcess
        if ($Existing) {
            & taskkill.exe /PID $Existing.Id /T /F | Out-Null
            Write-Host "Monitor stopped (PID $($Existing.Id))."
        } else {
            Write-Host "Monitor is not running."
        }
        Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    }
    "status" {
        $Existing = Get-MonitorProcess
        if ($Existing) {
            Write-Host "Monitor is RUNNING (PID $($Existing.Id), started $($Existing.StartTime.ToString('HH:mm:ss')))." 
        } else {
            Write-Host "Monitor is STOPPED."
        }
        # Show last scan time and product count from state file
        if (Test-Path $StateFile) {
            try {
                $State = Get-Content $StateFile -Raw | ConvertFrom-Json
                $Updated = [datetime]::Parse($State.updatedAt).ToLocalTime().ToString('yyyy-MM-dd HH:mm:ss')
                $Count = @($State.products).Count
                Write-Host "Last scan:    $Updated"
                Write-Host "Products:     $Count"
            } catch {}
        }
        if (Test-Path $LogFile) {
            Write-Host ""
            Write-Host "--- Last 5 log lines ---"
            Get-Content $LogFile -Tail 5
        }
    }
    "logs" {
        if (Test-Path $LogFile) { Get-Content $LogFile -Tail 80 }
        if ((Test-Path $ErrorLogFile) -and (Get-Item $ErrorLogFile).Length -gt 0) {
            Write-Host ""
            Write-Host "--- Error log ---"
            Get-Content $ErrorLogFile -Tail 20
        }
    }
    "help" { Write-Usage }
}
