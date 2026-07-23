param(
    [ValidateSet('run', 'test')]
    [string]$Mode = 'run'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$factoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$gptRoot = Join-Path $factoryRoot 'models\tts_tools\gpt_sovits'
$modelPython = Join-Path $gptRoot '.venv\Scripts\python.exe'
$backendPython = Join-Path $factoryRoot 'backend\.venv\Scripts\python.exe'
$voxcpmSource = Join-Path $factoryRoot 'models\tts_tools\voxcpm2\src'
$voxcpmWeights = Join-Path $factoryRoot 'models\tts\voxcpm2'
$voxcpmWorker = Join-Path $factoryRoot 'model_workers\voxcpm_server.py'
$viteScript = Join-Path $factoryRoot 'frontend\node_modules\vite\bin\vite.js'
$tscScript = Join-Path $factoryRoot 'frontend\node_modules\typescript\bin\tsc'
$nodePath = (Get-Command node.exe -ErrorAction Stop).Source

Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;

namespace ZwVoiceLauncher {
    public static class NativeJob {
        private const uint CREATE_SUSPENDED = 0x00000004;
        private const uint JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000;
        private const uint SYNCHRONIZE = 0x00100000;
        private const uint PROCESS_QUERY_LIMITED_INFORMATION = 0x00001000;
        private const uint INFINITE = 0xFFFFFFFF;
        private const int JobObjectExtendedLimitInformation = 9;

        [StructLayout(LayoutKind.Sequential)]
        private struct JOBOBJECT_BASIC_LIMIT_INFORMATION {
            public long PerProcessUserTimeLimit;
            public long PerJobUserTimeLimit;
            public uint LimitFlags;
            public UIntPtr MinimumWorkingSetSize;
            public UIntPtr MaximumWorkingSetSize;
            public uint ActiveProcessLimit;
            public UIntPtr Affinity;
            public uint PriorityClass;
            public uint SchedulingClass;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct IO_COUNTERS {
            public ulong ReadOperationCount;
            public ulong WriteOperationCount;
            public ulong OtherOperationCount;
            public ulong ReadTransferCount;
            public ulong WriteTransferCount;
            public ulong OtherTransferCount;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct JOBOBJECT_EXTENDED_LIMIT_INFORMATION {
            public JOBOBJECT_BASIC_LIMIT_INFORMATION BasicLimitInformation;
            public IO_COUNTERS IoInfo;
            public UIntPtr ProcessMemoryLimit;
            public UIntPtr JobMemoryLimit;
            public UIntPtr PeakProcessMemoryUsed;
            public UIntPtr PeakJobMemoryUsed;
        }

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        private struct STARTUPINFO {
            public int cb;
            public string lpReserved;
            public string lpDesktop;
            public string lpTitle;
            public uint dwX;
            public uint dwY;
            public uint dwXSize;
            public uint dwYSize;
            public uint dwXCountChars;
            public uint dwYCountChars;
            public uint dwFillAttribute;
            public uint dwFlags;
            public ushort wShowWindow;
            public ushort cbReserved2;
            public IntPtr lpReserved2;
            public IntPtr hStdInput;
            public IntPtr hStdOutput;
            public IntPtr hStdError;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct PROCESS_INFORMATION {
            public IntPtr hProcess;
            public IntPtr hThread;
            public uint dwProcessId;
            public uint dwThreadId;
        }

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern IntPtr CreateJobObject(IntPtr attributes, string name);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool SetInformationJobObject(IntPtr job, int infoClass, IntPtr info, uint length);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern bool CreateProcess(
            string applicationName,
            StringBuilder commandLine,
            IntPtr processAttributes,
            IntPtr threadAttributes,
            bool inheritHandles,
            uint creationFlags,
            IntPtr environment,
            string currentDirectory,
            ref STARTUPINFO startupInfo,
            out PROCESS_INFORMATION processInformation);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern uint ResumeThread(IntPtr thread);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool TerminateProcess(IntPtr process, uint exitCode);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern IntPtr OpenProcess(uint desiredAccess, bool inheritHandle, uint processId);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern uint WaitForSingleObject(IntPtr handle, uint milliseconds);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool GetExitCodeProcess(IntPtr process, out uint exitCode);

        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern bool CloseHandle(IntPtr handle);

        public static IntPtr CreateKillOnCloseJob() {
            IntPtr job = CreateJobObject(IntPtr.Zero, null);
            if (job == IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error());

            var limits = new JOBOBJECT_EXTENDED_LIMIT_INFORMATION();
            limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            int size = Marshal.SizeOf(typeof(JOBOBJECT_EXTENDED_LIMIT_INFORMATION));
            IntPtr pointer = Marshal.AllocHGlobal(size);
            try {
                Marshal.StructureToPtr(limits, pointer, false);
                if (!SetInformationJobObject(job, JobObjectExtendedLimitInformation, pointer, (uint)size))
                    throw new Win32Exception(Marshal.GetLastWin32Error());
            } finally {
                Marshal.FreeHGlobal(pointer);
            }
            return job;
        }

        public static int StartInJob(IntPtr job, string executable, string[] arguments, string workingDirectory) {
            var parts = new List<string>();
            parts.Add(QuoteArgument(executable));
            foreach (string argument in arguments) parts.Add(QuoteArgument(argument));
            var commandLine = new StringBuilder(string.Join(" ", parts));
            var startup = new STARTUPINFO();
            startup.cb = Marshal.SizeOf(typeof(STARTUPINFO));
            PROCESS_INFORMATION process;
            if (!CreateProcess(executable, commandLine, IntPtr.Zero, IntPtr.Zero, false, CREATE_SUSPENDED,
                               IntPtr.Zero, workingDirectory, ref startup, out process))
                throw new Win32Exception(Marshal.GetLastWin32Error());
            try {
                if (!AssignProcessToJobObject(job, process.hProcess)) {
                    int error = Marshal.GetLastWin32Error();
                    TerminateProcess(process.hProcess, 1);
                    throw new Win32Exception(error);
                }
                if (ResumeThread(process.hThread) == UInt32.MaxValue)
                    throw new Win32Exception(Marshal.GetLastWin32Error());
                return checked((int)process.dwProcessId);
            } finally {
                CloseHandle(process.hThread);
                CloseHandle(process.hProcess);
            }
        }

        public static int WaitForExitCode(int processId) {
            IntPtr process = OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, false, (uint)processId);
            if (process == IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error());
            try {
                WaitForSingleObject(process, INFINITE);
                uint exitCode;
                if (!GetExitCodeProcess(process, out exitCode))
                    throw new Win32Exception(Marshal.GetLastWin32Error());
                return unchecked((int)exitCode);
            } finally {
                CloseHandle(process);
            }
        }

        private static string QuoteArgument(string value) {
            if (value.Length > 0 && value.IndexOfAny(new [] {' ', '\t', '\n', '\v', '"'}) < 0) return value;
            var output = new StringBuilder();
            output.Append('"');
            int backslashes = 0;
            foreach (char character in value) {
                if (character == '\\') {
                    backslashes++;
                } else if (character == '"') {
                    output.Append('\\', backslashes * 2 + 1);
                    output.Append('"');
                    backslashes = 0;
                } else {
                    output.Append('\\', backslashes);
                    backslashes = 0;
                    output.Append(character);
                }
            }
            output.Append('\\', backslashes * 2);
            output.Append('"');
            return output.ToString();
        }
    }
}
'@

$jobHandle = [IntPtr]::Zero
$managedProcesses = [System.Collections.Generic.List[System.Diagnostics.Process]]::new()
$exitCode = 0

function Assert-File([string]$Path, [string]$Label) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Label was not found: $Path"
    }
}

function Assert-Directory([string]$Path, [string]$Label) {
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        throw "$Label was not found: $Path"
    }
}

function Assert-PortAvailable([int]$Port) {
    $listener = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
    if ($null -ne $listener) {
        $processIds = ($listener | Select-Object -ExpandProperty OwningProcess -Unique) -join ', '
        throw "Port $Port is already owned by process $processIds. Stop it first; this launcher will not kill unrelated processes."
    }
}

function Start-ManagedProcess {
    param(
        [string]$Name,
        [string]$Executable,
        [string[]]$Arguments,
        [string]$WorkingDirectory
    )
    Write-Host "[START] $Name" -ForegroundColor Cyan
    $processId = [ZwVoiceLauncher.NativeJob]::StartInJob($jobHandle, $Executable, $Arguments, $WorkingDirectory)
    $process = [System.Diagnostics.Process]::GetProcessById($processId)
    $managedProcesses.Add($process)
    return $process
}

function Wait-ServiceReady {
    param(
        [string]$Name,
        [string]$Uri,
        [System.Diagnostics.Process]$Process,
        [int]$TimeoutSeconds
    )
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        $Process.Refresh()
        if ($Process.HasExited) {
            throw "$Name exited during preload with code $($Process.ExitCode)"
        }
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $Uri -TimeoutSec 3
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 300) {
                Write-Host "[READY] $Name" -ForegroundColor Green
                return
            }
        } catch {
            # The model is still loading; its own console output explains the current stage.
        }
        Start-Sleep -Seconds 2
    }
    throw "$Name did not become ready within $TimeoutSeconds seconds: $Uri"
}

function Invoke-ManagedTest {
    param(
        [string]$Name,
        [string]$Executable,
        [string[]]$Arguments,
        [string]$WorkingDirectory
    )
    Write-Host "[TEST] $Name" -ForegroundColor Yellow
    $process = Start-ManagedProcess -Name $Name -Executable $Executable -Arguments $Arguments -WorkingDirectory $WorkingDirectory
    $testExitCode = [ZwVoiceLauncher.NativeJob]::WaitForExitCode($process.Id)
    if ($testExitCode -ne 0) {
        throw "$Name failed with exit code $testExitCode"
    }
    Write-Host "[PASS] $Name" -ForegroundColor Green
}

try {
    Assert-File $modelPython 'Model Python'
    Assert-File $backendPython 'Backend Python'
    Assert-File $voxcpmWorker 'VoxCPM2 Worker'
    Assert-File $viteScript 'Vite'
    Assert-File $tscScript 'TypeScript'
    Assert-Directory $voxcpmSource 'VoxCPM2 source'
    Assert-Directory $voxcpmWeights 'VoxCPM2 weights'
    foreach ($port in @(9880, 9881, 8800, 5173)) { Assert-PortAvailable $port }

    $jobHandle = [ZwVoiceLauncher.NativeJob]::CreateKillOnCloseJob()
    $env:PYTHONUNBUFFERED = '1'
    $env:PYTHONIOENCODING = 'utf-8'
    $env:ZW_VOICE_LAUNCHER_MANAGED = '1'
    $env:ZW_VOICE_GPT_SOVITS_URL = 'http://127.0.0.1:9880'
    $env:ZW_VOICE_VOXCPM_URL = 'http://127.0.0.1:9881'

    Write-Host 'Starting Zw Voice Factory. Initial model preload may take several minutes.' -ForegroundColor White

    $gptProcess = Start-ManagedProcess -Name 'GPT-SoVITS preload' -Executable $modelPython -Arguments @(
        'api_v2.py', '-a', '127.0.0.1', '-p', '9880', '-c', 'GPT_SoVITS/configs/tts_infer.yaml'
    ) -WorkingDirectory $gptRoot
    Wait-ServiceReady -Name 'GPT-SoVITS' -Uri 'http://127.0.0.1:9880/openapi.json' -Process $gptProcess -TimeoutSeconds 600

    $previousPythonPath = $env:PYTHONPATH
    $env:PYTHONPATH = $voxcpmSource
    try {
        $voxcpmProcess = Start-ManagedProcess -Name 'VoxCPM2 preload' -Executable $modelPython -Arguments @(
            $voxcpmWorker, '--model-path', $voxcpmWeights, '--host', '127.0.0.1', '--port', '9881', '--device', 'cuda'
        ) -WorkingDirectory $factoryRoot
    } finally {
        $env:PYTHONPATH = $previousPythonPath
    }
    Wait-ServiceReady -Name 'VoxCPM2' -Uri 'http://127.0.0.1:9881/health' -Process $voxcpmProcess -TimeoutSeconds 600

    $backendProcess = Start-ManagedProcess -Name 'FastAPI' -Executable $backendPython -Arguments @(
        '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8800'
    ) -WorkingDirectory (Join-Path $factoryRoot 'backend')
    Wait-ServiceReady -Name 'FastAPI' -Uri 'http://127.0.0.1:8800/api/health' -Process $backendProcess -TimeoutSeconds 90

    $frontendProcess = Start-ManagedProcess -Name 'Vite WebUI' -Executable $nodePath -Arguments @(
        $viteScript, '--host', '127.0.0.1', '--port', '5173', '--strictPort'
    ) -WorkingDirectory (Join-Path $factoryRoot 'frontend')
    Wait-ServiceReady -Name 'WebUI' -Uri 'http://127.0.0.1:5173/' -Process $frontendProcess -TimeoutSeconds 90

    if ($Mode -eq 'test') {
        Invoke-ManagedTest -Name 'Backend pytest' -Executable $backendPython -Arguments @(
            '-m', 'pytest', '-q'
        ) -WorkingDirectory (Join-Path $factoryRoot 'backend')
        Invoke-ManagedTest -Name 'Frontend typecheck' -Executable $nodePath -Arguments @(
            $tscScript, '-b'
        ) -WorkingDirectory (Join-Path $factoryRoot 'frontend')
        Invoke-ManagedTest -Name 'Frontend production build' -Executable $nodePath -Arguments @(
            $viteScript, 'build'
        ) -WorkingDirectory (Join-Path $factoryRoot 'frontend')
        Write-Host 'Startup, model preload, runtime checks, and automated tests all passed.' -ForegroundColor Green
    } else {
        Write-Host 'WebUI is ready: http://127.0.0.1:5173/' -ForegroundColor Green
        Write-Host 'Close this window or press Ctrl+C to stop all managed services. Generation progress appears here.' -ForegroundColor DarkGray
        Start-Process 'http://127.0.0.1:5173/'
        while ($true) {
            foreach ($process in $managedProcesses) {
                $process.Refresh()
                if ($process.HasExited) {
                    throw "Managed service PID $($process.Id) exited unexpectedly with code $($process.ExitCode)"
                }
            }
            Start-Sleep -Seconds 2
        }
    }
} catch {
    $exitCode = 1
    Write-Host "[FAILED] $($_.Exception.Message)" -ForegroundColor Red
} finally {
    if ($jobHandle -ne [IntPtr]::Zero) {
        Write-Host 'Stopping Zw Voice Factory child processes...' -ForegroundColor DarkGray
        [void][ZwVoiceLauncher.NativeJob]::CloseHandle($jobHandle)
    }
}

exit $exitCode
