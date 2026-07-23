param(
    [ValidateSet('run', 'test', 'status', 'stop', 'focus')]
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
$launcherStateRoot = Join-Path $factoryRoot 'outputs\runtime'
$launcherStatePath = Join-Path $launcherStateRoot 'launcher.json'
$webUrl = 'http://127.0.0.1:5173/'
$runtimeUrl = 'http://127.0.0.1:8800/api/runtime'

function Get-HealthyFactoryRuntime {
    try {
        $backend = Invoke-RestMethod -Uri 'http://127.0.0.1:8800/api/health' -TimeoutSec 2 -ErrorAction Stop
        if ($backend.launcher_managed -ne $true) {
            throw '后端不是由当前启动器管理。'
        }
        $voxcpm = Invoke-RestMethod -Uri 'http://127.0.0.1:9881/health' -TimeoutSec 2 -ErrorAction Stop
        $gpt = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:9880/openapi.json' -TimeoutSec 2 -ErrorAction Stop
        $frontend = Invoke-WebRequest -UseBasicParsing -Uri $webUrl -TimeoutSec 3 -ErrorAction Stop
        if (
            $frontend.StatusCode -eq 200 -and
            $voxcpm.status -eq 'ready' -and
            $gpt.StatusCode -eq 200
        ) {
            return [pscustomobject]@{
                launcher_managed = $true
                services = [pscustomobject]@{
                    voxcpm2 = [pscustomobject]@{ status = 'ready'; url = 'http://127.0.0.1:9881' }
                    gpt_sovits = [pscustomobject]@{ status = 'ready'; url = 'http://127.0.0.1:9880' }
                }
            }
        }
    } catch {
        # Compatibility path for a runtime started by the previous launcher version.
        try {
            $runtime = Invoke-RestMethod -Uri $runtimeUrl -TimeoutSec 8 -ErrorAction Stop
            $frontend = Invoke-WebRequest -UseBasicParsing -Uri $webUrl -TimeoutSec 3 -ErrorAction Stop
            if (
                $frontend.StatusCode -eq 200 -and
                $runtime.launcher_managed -eq $true -and
                $runtime.services.voxcpm2.status -eq 'ready' -and
                $runtime.services.gpt_sovits.status -eq 'ready'
            ) {
                return $runtime
            }
        } catch {
            return $null
        }
    }
    return $null
}

function Read-LauncherState {
    if (-not (Test-Path -LiteralPath $launcherStatePath -PathType Leaf)) {
        return $null
    }
    try {
        return Get-Content -Raw -LiteralPath $launcherStatePath -Encoding UTF8 | ConvertFrom-Json
    } catch {
        return $null
    }
}

function Write-RuntimeSummary($Runtime, $State) {
    $voxcpmStatus = if ($Runtime.services.voxcpm2.status -eq 'ready') { '就绪' } else { $Runtime.services.voxcpm2.status }
    $gptStatus = if ($Runtime.services.gpt_sovits.status -eq 'ready') { '就绪' } else { $Runtime.services.gpt_sovits.status }
    Write-Host 'Zw Voice Factory 正在运行。' -ForegroundColor Green
    Write-Host "WebUI      : $webUrl"
    Write-Host "VoxCPM2    : $voxcpmStatus"
    Write-Host "GPT-SoVITS : $gptStatus"
    if ($null -ne $State) {
        Write-Host "启动器 PID : $($State.launcher_pid)"
    }
}

function Write-LauncherState([hashtable]$Processes) {
    New-Item -ItemType Directory -Path $launcherStateRoot -Force | Out-Null
    $payload = [ordered]@{
        schema_version = 1
        launcher_pid = $PID
        started_at = [DateTime]::UtcNow.ToString('o')
        web_url = $webUrl
        services = $Processes
    }
    $temporaryPath = "$launcherStatePath.tmp"
    $payload | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $temporaryPath -Encoding UTF8
    Move-Item -LiteralPath $temporaryPath -Destination $launcherStatePath -Force
}

function Remove-LauncherStateIfOwned {
    $state = Read-LauncherState
    if ($null -ne $state -and [int]$state.launcher_pid -eq $PID) {
        Remove-Item -LiteralPath $launcherStatePath -Force -ErrorAction SilentlyContinue
    }
}

function Get-RecordedLauncher {
    $state = Read-LauncherState
    if ($null -eq $state) {
        return $null
    }
    $launcherPid = [int]$state.launcher_pid
    $launcherProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $launcherPid" -ErrorAction SilentlyContinue
    $expectedScript = Join-Path $factoryRoot 'scripts\start_factory.ps1'
    if (
        $null -eq $launcherProcess -or
        $launcherProcess.Name -ne 'powershell.exe' -or
        $launcherProcess.CommandLine -notlike "*$expectedScript*"
    ) {
        return $null
    }
    return [pscustomobject]@{ state = $state; process = $launcherProcess }
}

function Focus-RecordedLauncher {
    $recordedLauncher = Get-RecordedLauncher
    if ($null -eq $recordedLauncher) {
        return $false
    }
    $consoleProcess = Get-Process -Id $recordedLauncher.process.ParentProcessId -ErrorAction SilentlyContinue
    if ($null -eq $consoleProcess -or $consoleProcess.MainWindowHandle -eq [IntPtr]::Zero) {
        return $false
    }
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class ZwVoiceWindow {
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr handle);
    [DllImport("user32.dll")] public static extern bool ShowWindowAsync(IntPtr handle, int command);
}
'@
    [void][ZwVoiceWindow]::ShowWindowAsync($consoleProcess.MainWindowHandle, 9)
    [void][ZwVoiceWindow]::SetForegroundWindow($consoleProcess.MainWindowHandle)
    Write-Host "已激活现有启动器窗口（PID $($recordedLauncher.state.launcher_pid)）。" -ForegroundColor Green
    if ($null -ne (Get-HealthyFactoryRuntime)) {
        Start-Process $webUrl
    }
    return $true
}

function Stop-RecordedLauncher {
    $state = Read-LauncherState
    $runtime = Get-HealthyFactoryRuntime
    if ($null -eq $state) {
        if ($null -ne $runtime) {
            throw '检测到运行中的服务，但缺少启动器状态文件。请关闭最初的启动器窗口。'
        }
        Write-Host 'Zw Voice Factory 当前未运行。' -ForegroundColor DarkGray
        return
    }

    $recordedLauncher = Get-RecordedLauncher
    if ($null -eq $recordedLauncher) {
        $launcherPid = [int]$state.launcher_pid
        if ($null -ne $runtime) {
            throw "状态文件中的 PID $launcherPid 不属于本项目启动器，未停止任何进程。"
        }
        Remove-Item -LiteralPath $launcherStatePath -Force -ErrorAction SilentlyContinue
        Write-Host '已清理过期的启动器状态，Zw Voice Factory 当前未运行。' -ForegroundColor DarkGray
        return
    }

    $launcherPid = [int]$recordedLauncher.state.launcher_pid
    Write-Host "正在关闭启动器 PID $launcherPid 及其托管服务..." -ForegroundColor Yellow
    Stop-Process -Id $launcherPid -Force -ErrorAction Stop
    $deadline = [DateTime]::UtcNow.AddSeconds(20)
    do {
        Start-Sleep -Milliseconds 300
        $listeners = Get-NetTCPConnection -State Listen -LocalPort 5173, 8800, 9880, 9881 -ErrorAction SilentlyContinue
    } while ($listeners -and [DateTime]::UtcNow -lt $deadline)
    if ($listeners) {
        throw '启动器已停止，但一个或多个托管端口在 20 秒内未关闭。'
    }
    Remove-Item -LiteralPath $launcherStatePath -Force -ErrorAction SilentlyContinue
    Write-Host 'Zw Voice Factory 已完全关闭。' -ForegroundColor Green
}

if ($Mode -eq 'status') {
    $existingRuntime = Get-HealthyFactoryRuntime
    if ($null -eq $existingRuntime) {
        $recordedLauncher = Get-RecordedLauncher
        if ($null -ne $recordedLauncher) {
            Write-Host "Zw Voice Factory 正在启动，启动器 PID：$($recordedLauncher.state.launcher_pid)" -ForegroundColor Yellow
            exit 0
        }
        Write-Host 'Zw Voice Factory 当前未运行。' -ForegroundColor Yellow
        exit 1
    }
    Write-RuntimeSummary $existingRuntime (Read-LauncherState)
    exit 0
}

if ($Mode -eq 'stop') {
    try {
        Stop-RecordedLauncher
        exit 0
    } catch {
        Write-Host "[失败] $($_.Exception.Message)" -ForegroundColor Red
        exit 1
    }
}

if ($Mode -eq 'focus') {
    if (Focus-RecordedLauncher) {
        exit 0
    }
    exit 1
}

$existingRuntime = Get-HealthyFactoryRuntime
if ($null -ne $existingRuntime) {
    if ($Mode -eq 'test') {
        Write-Host '[失败] Zw Voice Factory 已在运行。请先关闭，再执行完整测试。' -ForegroundColor Red
        exit 1
    }
    Write-RuntimeSummary $existingRuntime (Read-LauncherState)
    Write-Host '检测到现有实例，正在打开 WebUI，不重复启动服务。' -ForegroundColor Cyan
    if ($env:ZW_VOICE_NONINTERACTIVE -ne '1') {
        Start-Process $webUrl
    }
    exit 0
}

$recordedLauncher = Get-RecordedLauncher
if ($null -ne $recordedLauncher) {
    if ($Mode -eq 'test') {
        Write-Host '[失败] Zw Voice Factory 正在启动。请先关闭，再执行完整测试。' -ForegroundColor Red
        exit 1
    }
    Write-Host "Zw Voice Factory 已在启动中，启动器 PID：$($recordedLauncher.state.launcher_pid)" -ForegroundColor Yellow
    Write-Host '请等待进程控制台提示 WebUI 已就绪。' -ForegroundColor DarkGray
    exit 0
}

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
        throw "未找到 $Label：$Path"
    }
}

function Assert-Directory([string]$Path, [string]$Label) {
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        throw "未找到 $Label：$Path"
    }
}

function Assert-PortAvailable([int]$Port) {
    $listener = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
    if ($null -ne $listener) {
        $processIds = ($listener | Select-Object -ExpandProperty OwningProcess -Unique) -join ', '
        throw "端口 $Port 已被进程 $processIds 占用。请先关闭对应程序；启动器不会结束无关进程。"
    }
}

function Start-ManagedProcess {
    param(
        [string]$Name,
        [string]$Executable,
        [string[]]$Arguments,
        [string]$WorkingDirectory
    )
    Write-Host "[启动] $Name" -ForegroundColor Cyan
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
            throw "$Name 在预加载期间退出，退出码：$($Process.ExitCode)"
        }
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $Uri -TimeoutSec 3
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 300) {
                Write-Host "[就绪] $Name" -ForegroundColor Green
                return
            }
        } catch {
            # The model is still loading; its own console output explains the current stage.
        }
        Start-Sleep -Seconds 2
    }
    throw "$Name 在 $TimeoutSeconds 秒内未就绪：$Uri"
}

function Invoke-ManagedTest {
    param(
        [string]$Name,
        [string]$Executable,
        [string[]]$Arguments,
        [string]$WorkingDirectory
    )
    Write-Host "[测试] $Name" -ForegroundColor Yellow
    $process = Start-ManagedProcess -Name $Name -Executable $Executable -Arguments $Arguments -WorkingDirectory $WorkingDirectory
    $testExitCode = [ZwVoiceLauncher.NativeJob]::WaitForExitCode($process.Id)
    if ($testExitCode -ne 0) {
        throw "$Name 执行失败，退出码：$testExitCode"
    }
    Write-Host "[通过] $Name" -ForegroundColor Green
}

try {
    Assert-File $modelPython '模型 Python 环境'
    Assert-File $backendPython '后端 Python 环境'
    Assert-File $voxcpmWorker 'VoxCPM2 工作服务'
    Assert-File $viteScript 'Vite'
    Assert-File $tscScript 'TypeScript'
    Assert-Directory $voxcpmSource 'VoxCPM2 源码目录'
    Assert-Directory $voxcpmWeights 'VoxCPM2 权重目录'
    foreach ($port in @(9880, 9881, 8800, 5173)) { Assert-PortAvailable $port }

    $jobHandle = [ZwVoiceLauncher.NativeJob]::CreateKillOnCloseJob()
    Write-LauncherState @{}
    $env:PYTHONUNBUFFERED = '1'
    $env:PYTHONIOENCODING = 'utf-8'
    $env:ZW_VOICE_LAUNCHER_MANAGED = '1'
    $env:ZW_VOICE_GPT_SOVITS_URL = 'http://127.0.0.1:9880'
    $env:ZW_VOICE_VOXCPM_URL = 'http://127.0.0.1:9881'

    Write-Host '正在启动 Zw Voice Factory，首次预加载模型可能需要数分钟。' -ForegroundColor White

    $gptProcess = Start-ManagedProcess -Name 'GPT-SoVITS 模型预加载' -Executable $modelPython -Arguments @(
        'api_v2.py', '-a', '127.0.0.1', '-p', '9880', '-c', 'GPT_SoVITS/configs/tts_infer.yaml'
    ) -WorkingDirectory $gptRoot
    Wait-ServiceReady -Name 'GPT-SoVITS' -Uri 'http://127.0.0.1:9880/openapi.json' -Process $gptProcess -TimeoutSeconds 600

    $previousPythonPath = $env:PYTHONPATH
    $env:PYTHONPATH = $voxcpmSource
    try {
        $voxcpmProcess = Start-ManagedProcess -Name 'VoxCPM2 模型预加载' -Executable $modelPython -Arguments @(
            $voxcpmWorker, '--model-path', $voxcpmWeights, '--host', '127.0.0.1', '--port', '9881', '--device', 'cuda'
        ) -WorkingDirectory $factoryRoot
    } finally {
        $env:PYTHONPATH = $previousPythonPath
    }
    Wait-ServiceReady -Name 'VoxCPM2' -Uri 'http://127.0.0.1:9881/health' -Process $voxcpmProcess -TimeoutSeconds 600

    $backendProcess = Start-ManagedProcess -Name 'FastAPI 后端服务' -Executable $backendPython -Arguments @(
        '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8800'
    ) -WorkingDirectory (Join-Path $factoryRoot 'backend')
    Wait-ServiceReady -Name 'FastAPI 后端服务' -Uri 'http://127.0.0.1:8800/api/health' -Process $backendProcess -TimeoutSeconds 90

    $frontendProcess = Start-ManagedProcess -Name 'Vite WebUI 前端服务' -Executable $nodePath -Arguments @(
        $viteScript, '--host', '127.0.0.1', '--port', '5173', '--strictPort'
    ) -WorkingDirectory (Join-Path $factoryRoot 'frontend')
    Wait-ServiceReady -Name 'WebUI 前端服务' -Uri 'http://127.0.0.1:5173/' -Process $frontendProcess -TimeoutSeconds 90

    Write-LauncherState @{
        gpt_sovits = $gptProcess.Id
        voxcpm2 = $voxcpmProcess.Id
        backend = $backendProcess.Id
        frontend = $frontendProcess.Id
    }

    if ($Mode -eq 'test') {
        $previousNonInteractive = $env:ZW_VOICE_NONINTERACTIVE
        $env:ZW_VOICE_NONINTERACTIVE = '1'
        try {
            Invoke-ManagedTest -Name '启动器单实例检查' -Executable $env:ComSpec -Arguments @(
                '/d', '/c', (Join-Path $factoryRoot 'Start-ZwVoice.cmd'), 'run'
            ) -WorkingDirectory $factoryRoot
        } finally {
            $env:ZW_VOICE_NONINTERACTIVE = $previousNonInteractive
        }
        Invoke-ManagedTest -Name '后端自动化测试' -Executable $backendPython -Arguments @(
            '-m', 'pytest', '-q'
        ) -WorkingDirectory (Join-Path $factoryRoot 'backend')
        Invoke-ManagedTest -Name '前端类型检查' -Executable $nodePath -Arguments @(
            $tscScript, '-b'
        ) -WorkingDirectory (Join-Path $factoryRoot 'frontend')
        Invoke-ManagedTest -Name '前端生产构建' -Executable $nodePath -Arguments @(
            $viteScript, 'build'
        ) -WorkingDirectory (Join-Path $factoryRoot 'frontend')
        Write-Host '启动、模型预加载、运行状态检查和自动化测试全部通过。' -ForegroundColor Green
    } else {
        Write-Host 'WebUI 已就绪：http://127.0.0.1:5173/' -ForegroundColor Green
        Write-Host '关闭此窗口或按 Ctrl+C，将同步关闭全部托管服务。音频生成进度会显示在这里。' -ForegroundColor DarkGray
        Start-Process 'http://127.0.0.1:5173/'
        while ($true) {
            foreach ($process in $managedProcesses) {
                $process.Refresh()
                if ($process.HasExited) {
                    throw "托管服务 PID $($process.Id) 意外退出，退出码：$($process.ExitCode)"
                }
            }
            Start-Sleep -Seconds 2
        }
    }
} catch {
    $exitCode = 1
    Write-Host "[失败] $($_.Exception.Message)" -ForegroundColor Red
} finally {
    Remove-LauncherStateIfOwned
    if ($jobHandle -ne [IntPtr]::Zero) {
        Write-Host '正在关闭 Zw Voice Factory 的全部子进程...' -ForegroundColor DarkGray
        [void][ZwVoiceLauncher.NativeJob]::CloseHandle($jobHandle)
    }
}

exit $exitCode
