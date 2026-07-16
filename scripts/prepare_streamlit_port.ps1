param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8501
)

$ErrorActionPreference = "Stop"

$listeners = @(
    Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
        Sort-Object OwningProcess -Unique
)

if ($listeners.Count -eq 0) {
    exit 0
}

$appProcesses = @()
$foreignProcesses = @()

foreach ($listener in $listeners) {
    $ownerPid = [int]$listener.OwningProcess
    $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $ownerPid" -ErrorAction SilentlyContinue
    if ($null -eq $processInfo) {
        continue
    }

    $commandLine = [string]$processInfo.CommandLine
    $normalizedCommand = $commandLine.Replace("\", "/")
    $isArtPmServer = (
        $normalizedCommand -match "(?i)streamlit" -and
        $normalizedCommand -match "(?i)artpm_agent/app\.py"
    )

    if ($isArtPmServer) {
        $appProcesses += $processInfo
    }
    else {
        $foreignProcesses += $processInfo
    }
}

if ($foreignProcesses.Count -gt 0) {
    $owners = $foreignProcesses | ForEach-Object {
        "PID $($_.ProcessId) ($($_.Name))"
    }
    Write-Error "Port $Port is already used by $($owners -join ', ')." -ErrorAction Continue
    exit 2
}

foreach ($processInfo in $appProcesses) {
    Write-Host "Stopping stale ArtPM server (PID $($processInfo.ProcessId))..."
    Stop-Process -Id $processInfo.ProcessId -Force -ErrorAction SilentlyContinue
}

$deadline = (Get-Date).AddSeconds(5)
do {
    $remaining = @(
        Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
    )
    if ($remaining.Count -eq 0) {
        exit 0
    }
    Start-Sleep -Milliseconds 100
} while ((Get-Date) -lt $deadline)

Write-Error "Port $Port was not released after stopping the stale ArtPM server." -ErrorAction Continue
exit 3
