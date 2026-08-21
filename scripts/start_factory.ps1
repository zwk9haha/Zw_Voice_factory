param(
    [ValidateSet('run', 'test', 'status', 'stop', 'focus', 'setup-analyzer')]
    [string]$Mode = 'run',
    [ValidateRange(0, 2147483647)]
    [int]$GuiOwnerPid = 0,
    [switch]$NoBrowser
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
$indexRoot = Join-Path $factoryRoot 'models\tts_tools\indextts2'
$indexPython = Join-Path $indexRoot '.venv\Scripts\python.exe'
$indexWeights = Join-Path $factoryRoot 'models\tts\IndexTeam--IndexTTS-2'
$indexWorker = Join-Path $factoryRoot 'model_workers\indextts_server.py'
$fastPython = Join-Path $env:USERPROFILE 'anaconda3\python.exe'
$fastWeights = Join-Path $factoryRoot 'models\tts\sherpa-onnx'
$fastWorker = Join-Path $factoryRoot 'model_workers\fast_tts_server.py'
$ollamaPath = (Get-Command ollama.exe -ErrorAction Stop).Source
$ollamaModelStore = Join-Path $factoryRoot 'local_models\ollama'
$ollamaModelName = 'zw-voice-analyzer:4b'
$ollamaUrl = 'http://127.0.0.1:11435'
$ollamaModelFile = Join-Path $factoryRoot 'config\voice_analyzer.Modelfile'
$quietOllamaRunner = Join-Path $factoryRoot 'scripts\run_quiet_ollama.ps1'
$windowsPowerShell = (Get-Command powershell.exe -ErrorAction Stop).Source
$viteScript = Join-Path $factoryRoot 'frontend\node_modules\vite\bin\vite.js'
$tscScript = Join-Path $factoryRoot 'frontend\node_modules\typescript\bin\tsc'
$themePaletteTest = Join-Path $factoryRoot 'frontend\scripts\verify-theme-palette.mjs'
$uiNoiseTest = Join-Path $factoryRoot 'frontend\scripts\verify-ui-noise.mjs'
$qualityWorkbenchTest = Join-Path $factoryRoot 'frontend\scripts\verify-quality-workbench.mjs'
$oneClickLauncherTest = Join-Path $factoryRoot 'frontend\scripts\verify-one-click-launcher.mjs'
$launcherStateRoot = Join-Path $factoryRoot 'outputs\runtime'
$runtimeLogRoot = Join-Path $factoryRoot 'outputs\logs\runtime'
$launcherStatePath = Join-Path $launcherStateRoot 'launcher.json'
$launcherMutexName = 'Local\ZwVoiceFactory-' + ($factoryRoot.ToLowerInvariant() -replace '[^a-z0-9]', '_')
$webUrl = 'http://127.0.0.1:5173/'
$runtimeUrl = 'http://127.0.0.1:8800/api/runtime'
$launcherMutex = $null
$ownsLauncherMutex = $false

function Get-HealthyFactoryRuntime {
    try {
        $backend = Invoke-RestMethod -Uri 'http://127.0.0.1:8800/api/health' -TimeoutSec 2 -ErrorAction Stop
        if ($backend.launcher_managed -ne $true) {
            throw '后端不是由当前启动器管理。'
        }
        $voxcpm = Invoke-RestMethod -Uri 'http://127.0.0.1:9881/health' -TimeoutSec 2 -ErrorAction Stop
        $index = Invoke-RestMethod -Uri 'http://127.0.0.1:9882/health' -TimeoutSec 2 -ErrorAction Stop
        $fast = Invoke-RestMethod -Uri 'http://127.0.0.1:9883/health' -TimeoutSec 2 -ErrorAction Stop
        $ollama = Invoke-RestMethod -Uri "$ollamaUrl/api/tags" -TimeoutSec 2 -ErrorAction Stop
        $gpt = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:9880/openapi.json' -TimeoutSec 2 -ErrorAction Stop
        $frontend = Invoke-WebRequest -UseBasicParsing -Uri $webUrl -TimeoutSec 3 -ErrorAction Stop
        if (
            $frontend.StatusCode -eq 200 -and
            $voxcpm.status -eq 'ready' -and
            $index.status -in @('idle', 'loading', 'ready') -and
            $fast.status -eq 'ready' -and
            $gpt.StatusCode -eq 200
        ) {
            $ollamaModels = @($ollama.models | ForEach-Object { $_.name })
            if ($ollamaModelName -notin $ollamaModels) {
                throw "本地音色分析模型未安装：$ollamaModelName"
            }
            return [pscustomobject]@{
                launcher_managed = $true
                services = [pscustomobject]@{
                    voxcpm2 = [pscustomobject]@{ status = 'ready'; url = 'http://127.0.0.1:9881' }
                    gpt_sovits = [pscustomobject]@{ status = 'ready'; url = 'http://127.0.0.1:9880' }
                    indextts2 = [pscustomobject]@{ status = $index.status; url = 'http://127.0.0.1:9882' }
                    fast_tts = [pscustomobject]@{ status = 'ready'; url = 'http://127.0.0.1:9883'; engine = $fast.engine }
                    local_analyzer = [pscustomobject]@{ status = 'ready'; url = $ollamaUrl; model = $ollamaModelName }
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
                $runtime.services.gpt_sovits.status -eq 'ready' -and
                $runtime.services.indextts2.status -eq 'ready' -and
                $runtime.services.fast_tts.status -eq 'ready' -and
                $runtime.services.local_analyzer.status -eq 'ready'
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

function Get-FactoryLauncherProcesses {
    $expectedScript = Join-Path $factoryRoot 'scripts\start_factory.ps1'
    return @(
        Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" -ErrorAction SilentlyContinue |
            Where-Object {
                [int]$_.ProcessId -ne $PID -and
                $_.CommandLine -like "*$expectedScript*" -and
                $_.CommandLine -match '(?i)\srun(?:\s|$)'
            }
    )
}

function Write-RuntimeSummary($Runtime, $State) {
    $voxcpmStatus = if ($Runtime.services.voxcpm2.status -eq 'ready') { '就绪' } else { $Runtime.services.voxcpm2.status }
    $gptStatus = if ($Runtime.services.gpt_sovits.status -eq 'ready') { '就绪' } else { $Runtime.services.gpt_sovits.status }
    $indexStatus = if ($Runtime.services.indextts2.status -eq 'idle') { '按需加载' } elseif ($Runtime.services.indextts2.status -eq 'ready') { '就绪' } else { $Runtime.services.indextts2.status }
    $fastStatus = if ($Runtime.services.fast_tts.status -eq 'ready') { '就绪' } else { $Runtime.services.fast_tts.status }
    $analyzerStatus = if ($Runtime.services.local_analyzer.status -eq 'ready') { '就绪' } else { $Runtime.services.local_analyzer.status }
    Write-Host 'Zw Voice Factory 正在运行。' -ForegroundColor Green
    Write-Host "WebUI      : $webUrl"
    Write-Host "VoxCPM2    : $voxcpmStatus"
    Write-Host "GPT-SoVITS : $gptStatus"
    Write-Host "IndexTTS2  : $indexStatus"
    Write-Host "轻量 TTS   : $fastStatus"
    Write-Host "本地音色分析: $analyzerStatus ($ollamaModelName)"
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
    if ($GuiOwnerPid -gt 0) {
        $payload.launcher_kind = 'gui'
        $payload.gui_owner_pid = $GuiOwnerPid
    } else {
        $payload.launcher_kind = 'console'
    }
    $temporaryPath = "$launcherStatePath.tmp"
    $payload | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $temporaryPath -Encoding UTF8
    Move-Item -LiteralPath $temporaryPath -Destination $launcherStatePath -Force
}

function Install-LocalAnalyzer {
    $temporaryServer = $null
    $previousHost = $env:OLLAMA_HOST
    $previousModels = $env:OLLAMA_MODELS
    $previousMaxLoaded = $env:OLLAMA_MAX_LOADED_MODELS
    $previousParallel = $env:OLLAMA_NUM_PARALLEL
    try {
        New-Item -ItemType Directory -Path $ollamaModelStore -Force | Out-Null
        $env:OLLAMA_HOST = '127.0.0.1:11435'
        $env:OLLAMA_MODELS = $ollamaModelStore
        $env:OLLAMA_MAX_LOADED_MODELS = '1'
        $env:OLLAMA_NUM_PARALLEL = '1'
        try {
            Invoke-WebRequest -UseBasicParsing -Uri "$ollamaUrl/api/tags" -TimeoutSec 2 | Out-Null
        } catch {
            $temporaryServer = Start-Process -FilePath $ollamaPath -ArgumentList 'serve' -WorkingDirectory $factoryRoot -WindowStyle Hidden -PassThru
            $deadline = [DateTime]::UtcNow.AddSeconds(30)
            do {
                Start-Sleep -Milliseconds 500
                try {
                    Invoke-WebRequest -UseBasicParsing -Uri "$ollamaUrl/api/tags" -TimeoutSec 2 | Out-Null
                    break
                } catch {
                    if ($temporaryServer.HasExited) {
                        throw "Ollama 本地分析服务启动失败，退出码：$($temporaryServer.ExitCode)"
                    }
                }
            } while ([DateTime]::UtcNow -lt $deadline)
            if ([DateTime]::UtcNow -ge $deadline) {
                throw 'Ollama 本地分析服务在 30 秒内未就绪。'
            }
        }

        Write-Host "[下载] qwen3.5:4b -> $ollamaModelStore" -ForegroundColor Cyan
        & $ollamaPath pull 'qwen3.5:4b'
        if ($LASTEXITCODE -ne 0) {
            throw "下载 qwen3.5:4b 失败，退出码：$LASTEXITCODE"
        }
        Write-Host "[创建] $ollamaModelName" -ForegroundColor Cyan
        & $ollamaPath create $ollamaModelName --file $ollamaModelFile
        if ($LASTEXITCODE -ne 0) {
            throw "创建 $ollamaModelName 失败，退出码：$LASTEXITCODE"
        }
        $models = @((Invoke-RestMethod -Uri "$ollamaUrl/api/tags" -TimeoutSec 5).models | ForEach-Object { $_.name })
        if ($ollamaModelName -notin $models) {
            throw "模型创建后未出现在项目仓库：$ollamaModelName"
        }
        Write-Host "[完成] 本地音色分析模型已部署：$ollamaModelName" -ForegroundColor Green
        Write-Host "模型目录：$ollamaModelStore" -ForegroundColor Green
    } finally {
        if ($null -ne $temporaryServer -and -not $temporaryServer.HasExited) {
            Stop-Process -Id $temporaryServer.Id -Force -ErrorAction SilentlyContinue
        }
        $env:OLLAMA_HOST = $previousHost
        $env:OLLAMA_MODELS = $previousModels
        $env:OLLAMA_MAX_LOADED_MODELS = $previousMaxLoaded
        $env:OLLAMA_NUM_PARALLEL = $previousParallel
    }
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
        $owners = @(Get-FactoryLauncherProcesses)
        if ($owners.Count -eq 1) {
            return [pscustomobject]@{
                state = [pscustomobject]@{ launcher_pid = [int]$owners[0].ProcessId }
                process = $owners[0]
            }
        }
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
        $owners = @(Get-FactoryLauncherProcesses)
        if ($owners.Count -eq 1) {
            return [pscustomobject]@{
                state = [pscustomobject]@{ launcher_pid = [int]$owners[0].ProcessId }
                process = $owners[0]
            }
        }
        return $null
    }
    return [pscustomobject]@{ state = $state; process = $launcherProcess }
}

function Focus-RecordedLauncher {
    $recordedLauncher = Get-RecordedLauncher
    if ($null -eq $recordedLauncher) {
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
    $ownerProcess = $null
    if (
        $null -ne $recordedLauncher.state.PSObject.Properties['launcher_kind'] -and
        $recordedLauncher.state.launcher_kind -eq 'gui' -and
        $null -ne $recordedLauncher.state.PSObject.Properties['gui_owner_pid']
    ) {
        $ownerProcess = Get-Process -Id ([int]$recordedLauncher.state.gui_owner_pid) -ErrorAction SilentlyContinue
    }
    if ($null -eq $ownerProcess) {
        $ownerProcess = Get-Process -Id $recordedLauncher.process.ParentProcessId -ErrorAction SilentlyContinue
    }
    if ($null -eq $ownerProcess -or $ownerProcess.MainWindowHandle -eq [IntPtr]::Zero) {
        return $false
    }
    [void][ZwVoiceWindow]::ShowWindowAsync($ownerProcess.MainWindowHandle, 9)
    [void][ZwVoiceWindow]::SetForegroundWindow($ownerProcess.MainWindowHandle)
    Write-Host "已激活现有启动器窗口（PID $($recordedLauncher.state.launcher_pid)）。" -ForegroundColor Green
    if ($null -ne (Get-HealthyFactoryRuntime)) {
        Start-Process $webUrl
    }
    return $true
}

function Stop-RecordedLauncher {
    $state = Read-LauncherState
    $runtime = Get-HealthyFactoryRuntime
    $recordedLauncher = Get-RecordedLauncher
    if ($null -eq $state -and $null -eq $recordedLauncher) {
        if ($null -ne $runtime) {
            throw '检测到运行中的服务，但缺少启动器状态文件。请关闭最初的启动器窗口。'
        }
        Write-Host 'Zw Voice Factory 当前未运行。' -ForegroundColor DarkGray
        return
    }

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
        $listeners = Get-NetTCPConnection -State Listen -LocalPort 5173, 8800, 9880, 9881, 9882, 9883, 11435 -ErrorAction SilentlyContinue
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

if ($Mode -eq 'setup-analyzer') {
    try {
        if ($null -ne (Get-HealthyFactoryRuntime)) {
            throw '请先使用 Start-ZwVoice.cmd stop 关闭工厂，再安装本地分析模型。'
        }
        if (-not (Test-Path -LiteralPath $ollamaModelFile -PathType Leaf)) {
            throw "未找到本地分析 Modelfile：$ollamaModelFile"
        }
        Install-LocalAnalyzer
        exit 0
    } catch {
        Write-Host "[失败] $($_.Exception.Message)" -ForegroundColor Red
        exit 1
    }
}

$existingRuntime = Get-HealthyFactoryRuntime
if ($null -ne $existingRuntime) {
    if ($Mode -eq 'test') {
        Write-Host '[失败] Zw Voice Factory 已在运行。请先关闭，再执行完整测试。' -ForegroundColor Red
        exit 1
    }
    Write-RuntimeSummary $existingRuntime (Read-LauncherState)
    Write-Host '检测到现有实例，正在打开 WebUI，不重复启动服务。' -ForegroundColor Cyan
    if ($env:ZW_VOICE_NONINTERACTIVE -ne '1' -and -not $NoBrowser) {
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

$launcherMutex = [System.Threading.Mutex]::new($false, $launcherMutexName)
try {
    $ownsLauncherMutex = $launcherMutex.WaitOne(0)
} catch [System.Threading.AbandonedMutexException] {
    $ownsLauncherMutex = $true
}
if (-not $ownsLauncherMutex) {
    Write-Host '另一个 Zw Voice Factory 启动器正在初始化，请等待其打开 WebUI。' -ForegroundColor Yellow
    $launcherMutex.Dispose()
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
$transcriptStarted = $false

if ($Mode -in @('run', 'test')) {
    New-Item -ItemType Directory -Path $runtimeLogRoot -Force | Out-Null
    $transcriptPath = Join-Path $runtimeLogRoot ("launcher-{0}-{1}.log" -f ([DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss')), $PID)
    try {
        Start-Transcript -LiteralPath $transcriptPath -Append | Out-Null
        $transcriptStarted = $true
    } catch {
        Write-Host "[日志] 启动器日志无法写入：$($_.Exception.Message)" -ForegroundColor Yellow
    }
}

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

function Write-StartupProgress {
    param(
        [ValidateRange(0, 100)]
        [int]$Percent,
        [string]$Message
    )
    $width = 28
    $filled = [Math]::Floor($width * $Percent / 100)
    $empty = $width - $filled
    $bar = ('#' * $filled) + ('-' * $empty)
    $color = if ($Percent -ge 100) { 'Green' } elseif ($Percent -ge 60) { 'Cyan' } else { 'DarkCyan' }
    Write-Host ("[{0}] {1,3}%  {2}" -f $bar, $Percent, $Message) -ForegroundColor $color
}

function Test-GuiOwnerAlive {
    if ($GuiOwnerPid -le 0) {
        return $true
    }
    return $null -ne (Get-Process -Id $GuiOwnerPid -ErrorAction SilentlyContinue)
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
        if (-not (Test-GuiOwnerAlive)) {
            throw '图形启动器已关闭，取消模型预加载。'
        }
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

function Test-LocalVoiceAnalyzer {
    Write-Host '[测试] 本地音色画像真实推理' -ForegroundColor Yellow
    $payload = @{
        character_id = 'smoke-test-character'
        display_name = '测试角色'
        aliases = @()
        mention_count = 6
        dialogue_count = 3
        gender_hint = 'male'
        evidence = @(
            '测试角色压低声音，平静地说道：“先确认出口，再决定是否行动。”'
            '他没有提高音量，只用清楚而短促的语句安排众人撤离。'
            '面对质疑，他停顿片刻，语气仍然克制而坚定。'
        )
    } | ConvertTo-Json -Depth 5
    $profileResponse = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8800/api/voice-analysis/preview' -Method Post -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($payload)) -TimeoutSec 240
    $profile = [Text.Encoding]::UTF8.GetString($profileResponse.RawContentStream.ToArray()) | ConvertFrom-Json
    if (
        $profile.backend -ne 'local' -or
        $profile.model -ne $ollamaModelName -or
        @($profile.timbre_tags).Count -lt 5 -or
        @($profile.delivery_tags).Count -lt 5 -or
        [string]::IsNullOrWhiteSpace($profile.voice_prompt) -or
        $profile.voice_prompt.Length -lt 120 -or
        $profile.voice_prompt -notmatch '角色辨识核心：' -or
        $profile.voice_prompt -notmatch '稳定表达习惯：'
    ) {
        throw '本地音色分析烟测返回了不完整画像。'
    }
    Write-Host "[通过] 本地音色画像真实推理：$($profile.voice_prompt)" -ForegroundColor Green
}

function Wait-LocalAnalyzerUnloaded {
    Write-Host '[释放] 本地音色分析模型显存' -ForegroundColor Cyan
    $unloadPayload = @{
        model = $ollamaModelName
        keep_alive = '0s'
    } | ConvertTo-Json
    Invoke-WebRequest -UseBasicParsing -Uri "$ollamaUrl/api/generate" -Method Post -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($unloadPayload)) -TimeoutSec 30 | Out-Null

    $deadline = [DateTime]::UtcNow.AddSeconds(60)
    while ([DateTime]::UtcNow -lt $deadline) {
        $loadedModels = @((Invoke-RestMethod -Uri "$ollamaUrl/api/ps" -TimeoutSec 3).models | ForEach-Object { $_.name })
        if ($ollamaModelName -notin $loadedModels) {
            Start-Sleep -Seconds 2
            Write-Host '[就绪] 本地音色分析模型显存已释放' -ForegroundColor Green
            return
        }
        Start-Sleep -Milliseconds 500
    }
    throw '本地音色分析模型未能在 60 秒内释放显存。'
}

try {
    Assert-File $modelPython '模型 Python 环境'
    Assert-File $backendPython '后端 Python 环境'
    Assert-File $voxcpmWorker 'VoxCPM2 工作服务'
    Assert-File $indexPython 'IndexTTS2 Python 环境'
    Assert-File $indexWorker 'IndexTTS2 工作服务'
    Assert-File $fastPython '轻量 TTS Python 环境'
    Assert-File $fastWorker '轻量 TTS 工作服务'
    Assert-File $ollamaPath 'Ollama 本地分析运行时'
    Assert-File $viteScript 'Vite'
    Assert-File $tscScript 'TypeScript'
    if ($Mode -eq 'test') {
        Assert-File $themePaletteTest '主题配色回归检查'
        Assert-File $uiNoiseTest '界面噪声回归检查'
        Assert-File $qualityWorkbenchTest '质量渲染交互回归检查'
        Assert-File $oneClickLauncherTest '一键流程与图形启动器回归检查'
        Write-Host '[测试] 主题配色隔离' -ForegroundColor Yellow
        & $nodePath $themePaletteTest
        if ($LASTEXITCODE -ne 0) {
            throw "主题配色隔离执行失败，退出码：$LASTEXITCODE"
        }
        Write-Host '[通过] 主题配色隔离' -ForegroundColor Green
        Write-Host '[测试] 音频主题与运行日志降噪' -ForegroundColor Yellow
        & $nodePath $uiNoiseTest
        if ($LASTEXITCODE -ne 0) {
            throw "音频主题与运行日志降噪执行失败，退出码：$LASTEXITCODE"
        }
        Write-Host '[通过] 音频主题与运行日志降噪' -ForegroundColor Green
        Write-Host '[测试] 质量声线路由与流播缓存复用' -ForegroundColor Yellow
        & $nodePath $qualityWorkbenchTest
        if ($LASTEXITCODE -ne 0) {
            throw "质量声线路由与流播缓存复用执行失败，退出码：$LASTEXITCODE"
        }
        Write-Host '[通过] 质量声线路由与流播缓存复用' -ForegroundColor Green
        Write-Host '[测试] 一键流程跳转与图形启动器配色' -ForegroundColor Yellow
        & $nodePath $oneClickLauncherTest
        if ($LASTEXITCODE -ne 0) {
            throw "一键流程与图形启动器回归检查执行失败，退出码：$LASTEXITCODE"
        }
        Write-Host '[通过] 一键流程跳转与图形启动器配色' -ForegroundColor Green
    }
    Assert-Directory $voxcpmSource 'VoxCPM2 源码目录'
    Assert-Directory $voxcpmWeights 'VoxCPM2 权重目录'
    Assert-Directory $indexWeights 'IndexTTS2 权重目录'
    Assert-Directory $fastWeights 'Sherpa ONNX 轻量 TTS 权重目录'
    Assert-Directory $ollamaModelStore '项目内 Ollama 模型目录，请先运行 Start-ZwVoice.cmd setup-analyzer'
    foreach ($port in @(9880, 9881, 9882, 9883, 11435, 8800, 5173)) { Assert-PortAvailable $port }

    $jobHandle = [ZwVoiceLauncher.NativeJob]::CreateKillOnCloseJob()
    if ($Mode -eq 'test') {
        Write-Host '[绑定] 自动化测试由启动器 Job Object 托管，测试结束自动关闭全部子进程。' -ForegroundColor DarkCyan
    } else {
        Write-Host '[绑定] 运行服务由启动器 Job Object 托管，关闭启动器同步关闭全部子进程。' -ForegroundColor DarkCyan
    }
    Write-LauncherState @{}
    $env:PYTHONUNBUFFERED = '1'
    $env:PYTHONIOENCODING = 'utf-8'
    $env:ZW_VOICE_LAUNCHER_MANAGED = '1'
    $env:ZW_VOICE_GPT_SOVITS_URL = 'http://127.0.0.1:9880'
    $env:ZW_VOICE_VOXCPM_URL = 'http://127.0.0.1:9881'
    $env:ZW_VOICE_INDEXTTS_URL = 'http://127.0.0.1:9882'
    $env:ZW_VOICE_FAST_TTS_URL = 'http://127.0.0.1:9883'
    $env:ZW_VOICE_ANALYZER_BACKEND = 'local'
    if ($Mode -eq 'test') {
        $env:ZW_VOICE_ANALYZER_FORCE_BACKEND = 'local'
    }
    $env:ZW_VOICE_OLLAMA_URL = $ollamaUrl
    $env:ZW_VOICE_OLLAMA_MODEL = $ollamaModelName
    $env:OLLAMA_HOST = '127.0.0.1:11435'
    $env:OLLAMA_MODELS = $ollamaModelStore
    $env:OLLAMA_MAX_LOADED_MODELS = '1'
    $env:OLLAMA_NUM_PARALLEL = '1'
    $env:OLLAMA_KEEP_ALIVE = '0'
    $env:OLLAMA_NO_CLOUD = '1'

    Write-Host '正在启动 Zw Voice Factory，首次预加载模型可能需要数分钟。' -ForegroundColor White
    Write-StartupProgress -Percent 0 -Message '初始化模型运行环境'

    Write-StartupProgress -Percent 5 -Message '启动本地音色分析服务'
    Assert-File $quietOllamaRunner 'Ollama 控制台降噪包装器'
    $ollamaRuntimeLog = Join-Path $runtimeLogRoot ("ollama-{0}-{1}.log" -f ([DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss')), $PID)
    $ollamaProcess = Start-ManagedProcess -Name 'Ollama 本地音色分析服务' -Executable $windowsPowerShell -Arguments @(
        '-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $quietOllamaRunner,
        '-OllamaPath', $ollamaPath, '-LogPath', $ollamaRuntimeLog
    ) -WorkingDirectory $factoryRoot
    Wait-ServiceReady -Name 'Ollama 本地音色分析服务' -Uri "$ollamaUrl/api/tags" -Process $ollamaProcess -TimeoutSeconds 60
    $installedAnalyzerModels = @((Invoke-RestMethod -Uri "$ollamaUrl/api/tags" -TimeoutSec 5).models | ForEach-Object { $_.name })
    if ($ollamaModelName -notin $installedAnalyzerModels) {
        throw "未安装 $ollamaModelName。请先运行 Start-ZwVoice.cmd setup-analyzer。"
    }
    Write-StartupProgress -Percent 15 -Message '本地音色分析服务已就绪'

    Write-StartupProgress -Percent 20 -Message '启动项目后端'
    $backendProcess = Start-ManagedProcess -Name 'FastAPI 后端服务' -Executable $backendPython -Arguments @(
        '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8800', '--no-access-log'
    ) -WorkingDirectory (Join-Path $factoryRoot 'backend')
    Wait-ServiceReady -Name 'FastAPI 后端服务' -Uri 'http://127.0.0.1:8800/api/health' -Process $backendProcess -TimeoutSeconds 90
    if ($Mode -eq 'test') {
        Remove-Item Env:ZW_VOICE_ANALYZER_FORCE_BACKEND -ErrorAction SilentlyContinue
    }
    Write-StartupProgress -Percent 28 -Message '项目后端已就绪'
    if ($Mode -eq 'test') {
        Test-LocalVoiceAnalyzer
        Wait-LocalAnalyzerUnloaded
    }

    Write-StartupProgress -Percent 35 -Message '加载 GPT-SoVITS 模型'
    $previousNumbaDisableJit = $env:NUMBA_DISABLE_JIT
    $env:NUMBA_DISABLE_JIT = '1'
    try {
        $gptProcess = Start-ManagedProcess -Name 'GPT-SoVITS 模型预加载' -Executable $modelPython -Arguments @(
            'api_v2.py', '-a', '127.0.0.1', '-p', '9880', '-c', 'GPT_SoVITS/configs/tts_infer.yaml'
        ) -WorkingDirectory $gptRoot
    } finally {
        $env:NUMBA_DISABLE_JIT = $previousNumbaDisableJit
    }
    Wait-ServiceReady -Name 'GPT-SoVITS' -Uri 'http://127.0.0.1:9880/openapi.json' -Process $gptProcess -TimeoutSeconds 900
    Write-StartupProgress -Percent 58 -Message 'GPT-SoVITS 模型已就绪'

    Write-StartupProgress -Percent 62 -Message '加载 VoxCPM2 模型'
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
    Write-StartupProgress -Percent 82 -Message 'VoxCPM2 模型已就绪'

    Write-StartupProgress -Percent 84 -Message '加载极速路线轻量 TTS'
    $fastProcess = Start-ManagedProcess -Name 'Sherpa ONNX 轻量 TTS' -Executable $fastPython -Arguments @(
        $fastWorker, '--model-path', $fastWeights, '--host', '127.0.0.1', '--port', '9883', '--threads', '2'
    ) -WorkingDirectory $factoryRoot
    Wait-ServiceReady -Name '轻量 TTS' -Uri 'http://127.0.0.1:9883/health' -Process $fastProcess -TimeoutSeconds 120
    Write-StartupProgress -Percent 88 -Message '极速路线轻量 TTS 已就绪'

    Write-StartupProgress -Percent 90 -Message '启动 IndexTTS2 按需服务'
    $indexProcess = Start-ManagedProcess -Name 'IndexTTS2 按需服务' -Executable $indexPython -Arguments @(
        $indexWorker, '--model-path', $indexWeights, '--tool-root', $indexRoot, '--host', '127.0.0.1', '--port', '9882', '--device', 'cpu'
    ) -WorkingDirectory $indexRoot
    Wait-ServiceReady -Name 'IndexTTS2 按需服务' -Uri 'http://127.0.0.1:9882/health' -Process $indexProcess -TimeoutSeconds 90
    Write-StartupProgress -Percent 94 -Message 'IndexTTS2 按需服务已就绪'

    Write-StartupProgress -Percent 95 -Message '启动 WebUI'
    $frontendProcess = Start-ManagedProcess -Name 'Vite WebUI 前端服务' -Executable $nodePath -Arguments @(
        $viteScript, '--host', '127.0.0.1', '--port', '5173', '--strictPort'
    ) -WorkingDirectory (Join-Path $factoryRoot 'frontend')
    Wait-ServiceReady -Name 'WebUI 前端服务' -Uri 'http://127.0.0.1:5173/' -Process $frontendProcess -TimeoutSeconds 90
    Write-StartupProgress -Percent 100 -Message '全部模型与 WebUI 已就绪'

    Write-LauncherState @{
        gpt_sovits = $gptProcess.Id
        voxcpm2 = $voxcpmProcess.Id
        indextts2 = $indexProcess.Id
        fast_tts = $fastProcess.Id
        local_analyzer = $ollamaProcess.Id
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
        $testTemp = Join-Path $factoryRoot "outputs\runtime\test-temp-$PID"
        New-Item -ItemType Directory -Path $testTemp -Force | Out-Null
        $previousTemp = $env:TEMP
        $previousTmp = $env:TMP
        $env:TEMP = $testTemp
        $env:TMP = $testTemp
        try {
            Invoke-ManagedTest -Name '后端自动化测试' -Executable $backendPython -Arguments @(
                '-m', 'pytest', '-q', '-p', 'no:cacheprovider', '--basetemp', (Join-Path $testTemp 'pytest')
            ) -WorkingDirectory (Join-Path $factoryRoot 'backend')
        } finally {
            $env:TEMP = $previousTemp
            $env:TMP = $previousTmp
        }
        Invoke-ManagedTest -Name '前端类型检查' -Executable $nodePath -Arguments @(
            $tscScript, '-b'
        ) -WorkingDirectory (Join-Path $factoryRoot 'frontend')
        Invoke-ManagedTest -Name '前端生产构建' -Executable $nodePath -Arguments @(
            $viteScript, 'build'
        ) -WorkingDirectory (Join-Path $factoryRoot 'frontend')
        Write-Host '启动、模型预加载、运行状态检查和自动化测试全部通过。' -ForegroundColor Green
    } else {
        Write-Host 'WebUI 已就绪：http://127.0.0.1:5173/' -ForegroundColor Green
        if ($GuiOwnerPid -gt 0) {
            Write-Host '图形启动器正在托管服务。关闭图形启动器将同步关闭全部托管服务。' -ForegroundColor DarkGray
        } else {
            Write-Host '关闭此窗口或按 Ctrl+C，将同步关闭全部托管服务。云端分析与音频生成进度会显示在这里。' -ForegroundColor DarkGray
        }
        if (-not $NoBrowser) {
            Start-Process 'http://127.0.0.1:5173/'
        }
        while ($true) {
            if (-not (Test-GuiOwnerAlive)) {
                throw '图形启动器已关闭，正在同步关闭全部托管服务。'
            }
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
    if ($transcriptStarted) {
        Stop-Transcript | Out-Null
    }
    Remove-LauncherStateIfOwned
    if ($jobHandle -ne [IntPtr]::Zero) {
        Write-Host '正在关闭 Zw Voice Factory 的全部子进程...' -ForegroundColor DarkGray
        [void][ZwVoiceLauncher.NativeJob]::CloseHandle($jobHandle)
    }
    if ($ownsLauncherMutex) {
        $launcherMutex.ReleaseMutex()
    }
    if ($null -ne $launcherMutex) {
        $launcherMutex.Dispose()
    }
}

exit $exitCode
