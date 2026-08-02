param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8501,

    [switch]$SelfTest
)

$ErrorActionPreference = "Stop"

function Get-NormalizedFullPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$InputPath
    )

    return [System.IO.Path]::GetFullPath($InputPath).TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    )
}

function Test-ArtPmServerCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$CommandLine,

        [AllowEmptyString()]
        [string]$WorkingDirectory,

        [Parameter(Mandatory = $true)]
        [string]$ProjectRoot
    )

    $normalizedCommand = $CommandLine.Replace("\", "/")
    if ($normalizedCommand -notmatch '(?i)(?:^|[/\s"''])streamlit(?:\.exe)?(?:[/\s"'']|$)') {
        return $false
    }

    $runMatch = [regex]::Match(
        $normalizedCommand,
        '(?i)(?:^|\s)run\s+(?:"(?<double>[^"]+)"|''(?<single>[^'']+)''|(?<bare>\S+))'
    )
    if (-not $runMatch.Success) {
        return $false
    }

    $scriptArgument = @(
        $runMatch.Groups["double"].Value,
        $runMatch.Groups["single"].Value,
        $runMatch.Groups["bare"].Value
    ) | Where-Object { $_ } | Select-Object -First 1

    try {
        if ([System.IO.Path]::IsPathRooted($scriptArgument)) {
            $scriptPath = Get-NormalizedFullPath -InputPath $scriptArgument
        }
        elseif (-not [string]::IsNullOrWhiteSpace($WorkingDirectory)) {
            $scriptPath = Get-NormalizedFullPath -InputPath (
                Join-Path -Path $WorkingDirectory -ChildPath $scriptArgument
            )
        }
        else {
            return $false
        }

        $normalizedProjectRoot = Get-NormalizedFullPath -InputPath $ProjectRoot
        $supportedEntrypoints = @(
            Get-NormalizedFullPath -InputPath (Join-Path $normalizedProjectRoot "app.py")
            Get-NormalizedFullPath -InputPath (Join-Path $normalizedProjectRoot "artpm_agent\app.py")
        )
    }
    catch {
        return $false
    }

    foreach ($entrypoint in $supportedEntrypoints) {
        if ([string]::Equals($scriptPath, $entrypoint, [System.StringComparison]::OrdinalIgnoreCase)) {
            return $true
        }
    }

    return $false
}

function Initialize-ProcessWorkingDirectoryReader {
    if ("ArtPm.Startup.ProcessWorkingDirectoryReader" -as [type]) {
        return
    }

    # Win32_Process omits the current directory, so read the process parameters
    # before resolving a relative app.py. If this lookup fails, the caller keeps
    # the listener classified as foreign and does not stop it.
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;

namespace ArtPm.Startup
{
    public static class ProcessWorkingDirectoryReader
    {
        [StructLayout(LayoutKind.Sequential)]
        private struct ProcessBasicInformation
        {
            public IntPtr Reserved1;
            public IntPtr PebBaseAddress;
            public IntPtr Reserved2_0;
            public IntPtr Reserved2_1;
            public IntPtr UniqueProcessId;
            public IntPtr Reserved3;
        }

        [DllImport("ntdll.dll")]
        private static extern int NtQueryInformationProcess(
            IntPtr processHandle,
            int processInformationClass,
            ref ProcessBasicInformation processInformation,
            int processInformationLength,
            out int returnLength);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool ReadProcessMemory(
            IntPtr processHandle,
            IntPtr baseAddress,
            byte[] buffer,
            int size,
            out IntPtr bytesRead);

        private static byte[] ReadBytes(IntPtr processHandle, IntPtr address, int length)
        {
            var buffer = new byte[length];
            IntPtr bytesRead;
            if (!ReadProcessMemory(processHandle, address, buffer, length, out bytesRead) ||
                bytesRead.ToInt64() != length)
            {
                throw new Win32Exception(Marshal.GetLastWin32Error());
            }
            return buffer;
        }

        private static IntPtr ReadPointer(IntPtr processHandle, IntPtr address)
        {
            var buffer = ReadBytes(processHandle, address, IntPtr.Size);
            return IntPtr.Size == 8
                ? new IntPtr(BitConverter.ToInt64(buffer, 0))
                : new IntPtr(BitConverter.ToInt32(buffer, 0));
        }

        public static string Get(int processId)
        {
            using (var process = Process.GetProcessById(processId))
            {
                var info = new ProcessBasicInformation();
                int returnLength;
                int status = NtQueryInformationProcess(
                    process.Handle,
                    0,
                    ref info,
                    Marshal.SizeOf(info),
                    out returnLength);
                if (status != 0)
                {
                    throw new InvalidOperationException(
                        "NtQueryInformationProcess failed: 0x" + status.ToString("X8"));
                }

                int processParametersOffset = IntPtr.Size == 8 ? 0x20 : 0x10;
                int currentDirectoryOffset = IntPtr.Size == 8 ? 0x38 : 0x24;
                IntPtr processParameters = ReadPointer(
                    process.Handle,
                    IntPtr.Add(info.PebBaseAddress, processParametersOffset));
                byte[] unicodeString = ReadBytes(
                    process.Handle,
                    IntPtr.Add(processParameters, currentDirectoryOffset),
                    IntPtr.Size == 8 ? 16 : 8);

                ushort length = BitConverter.ToUInt16(unicodeString, 0);
                long bufferAddress = IntPtr.Size == 8
                    ? BitConverter.ToInt64(unicodeString, 8)
                    : BitConverter.ToInt32(unicodeString, 4);
                if (length == 0 || bufferAddress == 0)
                {
                    return String.Empty;
                }

                return Encoding.Unicode.GetString(
                    ReadBytes(process.Handle, new IntPtr(bufferAddress), length));
            }
        }
    }
}
'@
}

function Get-ProcessWorkingDirectory {
    param(
        [Parameter(Mandatory = $true)]
        [int]$ProcessId
    )

    try {
        Initialize-ProcessWorkingDirectoryReader
        return [ArtPm.Startup.ProcessWorkingDirectoryReader]::Get($ProcessId)
    }
    catch {
        return $null
    }
}

function Invoke-SelfTest {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ProjectRoot
    )

    $otherRoot = Join-Path ([System.IO.Path]::GetTempPath()) "other-streamlit-project"
    $cases = @(
        @{
            Name = "package entrypoint in repository"
            CommandLine = 'python -m streamlit run artpm_agent/app.py --server.port 8501'
            WorkingDirectory = $ProjectRoot
            Expected = $true
        },
        @{
            Name = "legacy root entrypoint in repository"
            CommandLine = 'streamlit run app.py --server.port 8501'
            WorkingDirectory = $ProjectRoot
            Expected = $true
        },
        @{
            Name = "legacy root entrypoint in another directory"
            CommandLine = 'streamlit run app.py --server.port 8501'
            WorkingDirectory = $otherRoot
            Expected = $false
        },
        @{
            Name = "absolute app.py in another directory"
            CommandLine = "streamlit run `"$(Join-Path $otherRoot 'app.py')`" --server.port 8501"
            WorkingDirectory = $ProjectRoot
            Expected = $false
        },
        @{
            Name = "non-Streamlit process"
            CommandLine = 'python app.py'
            WorkingDirectory = $ProjectRoot
            Expected = $false
        }
    )

    $failures = 0
    foreach ($case in $cases) {
        $actual = Test-ArtPmServerCommand `
            -CommandLine $case.CommandLine `
            -WorkingDirectory $case.WorkingDirectory `
            -ProjectRoot $ProjectRoot
        if ($actual -ne $case.Expected) {
            Write-Host "[FAIL] $($case.Name): expected $($case.Expected), got $actual"
            $failures++
        }
        else {
            Write-Host "[PASS] $($case.Name)"
        }
    }

    $nativeDirectory = Get-ProcessWorkingDirectory -ProcessId $PID
    $expectedDirectory = Get-NormalizedFullPath -InputPath ([Environment]::CurrentDirectory)
    if ([string]::IsNullOrWhiteSpace($nativeDirectory) -or
        -not [string]::Equals(
            (Get-NormalizedFullPath -InputPath $nativeDirectory),
            $expectedDirectory,
            [System.StringComparison]::OrdinalIgnoreCase)) {
        Write-Host "[FAIL] native process working-directory lookup"
        $failures++
    }
    else {
        Write-Host "[PASS] native process working-directory lookup"
    }

    if ($failures -gt 0) {
        Write-Error "$failures prepare_streamlit_port self-test(s) failed." -ErrorAction Continue
        return 1
    }

    Write-Host "All prepare_streamlit_port self-tests passed."
    return 0
}

$projectRoot = Get-NormalizedFullPath -InputPath (Join-Path $PSScriptRoot "..")

if ($SelfTest) {
    exit (Invoke-SelfTest -ProjectRoot $projectRoot)
}

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
    $workingDirectory = Get-ProcessWorkingDirectory -ProcessId $ownerPid
    $isArtPmServer = Test-ArtPmServerCommand `
        -CommandLine $commandLine `
        -WorkingDirectory $workingDirectory `
        -ProjectRoot $projectRoot

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
