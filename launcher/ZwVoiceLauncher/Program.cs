using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace ZwVoiceLauncher;

internal static class Program
{
    private const string WindowTitle = "ZW 语音工厂";

    [STAThread]
    private static void Main(string[] args)
    {
        var root = FindFactoryRoot(args);
        if (root is null)
        {
            MessageBox.Show(
                "未找到 Zw Voice Factory 项目目录。请将“ZW语音工厂启动器.exe”放在项目根目录，或通过 --root 指定项目目录。",
                WindowTitle,
                MessageBoxButtons.OK,
                MessageBoxIcon.Error);
            return;
        }

        using var mutex = new Mutex(true, BuildMutexName(root), out var createdNew);
        if (!createdNew)
        {
            ActivateExistingLauncher();
            return;
        }

        ApplicationConfiguration.Initialize();
        Application.Run(new LauncherForm(root));
    }

    private static string? FindFactoryRoot(string[] args)
    {
        var requestedRoot = ReadArgument(args, "--root");
        if (!string.IsNullOrWhiteSpace(requestedRoot) && IsFactoryRoot(requestedRoot))
        {
            return Path.GetFullPath(requestedRoot);
        }

        var current = new DirectoryInfo(AppContext.BaseDirectory);
        while (current is not null)
        {
            if (IsFactoryRoot(current.FullName))
            {
                return current.FullName;
            }
            current = current.Parent;
        }
        return null;
    }

    private static string? ReadArgument(string[] args, string name)
    {
        for (var index = 0; index < args.Length - 1; index++)
        {
            if (string.Equals(args[index], name, StringComparison.OrdinalIgnoreCase))
            {
                return args[index + 1];
            }
        }
        return null;
    }

    private static bool IsFactoryRoot(string path) =>
        File.Exists(Path.Combine(path, "Start-ZwVoice.cmd")) &&
        File.Exists(Path.Combine(path, "scripts", "start_factory.ps1"));

    private static string BuildMutexName(string root)
    {
        var safeRoot = Regex.Replace(root.ToLowerInvariant(), "[^a-z0-9]", "_");
        return $"Local\\ZwVoiceFactoryGui_{safeRoot}";
    }

    private static void ActivateExistingLauncher()
    {
        var handle = NativeWindow.FindWindow(null, WindowTitle);
        if (handle != IntPtr.Zero)
        {
            NativeWindow.ShowWindowAsync(handle, NativeWindow.ShowNormal);
            NativeWindow.SetForegroundWindow(handle);
        }
    }
}

internal sealed class LauncherForm : Form
{
    private static readonly Color Background = LauncherTheme.Background;
    private static readonly Color Surface = LauncherTheme.Surface;
    private static readonly Color Primary = LauncherTheme.Primary;
    private static readonly Color Muted = LauncherTheme.Muted;
    private static readonly Color UiText = LauncherTheme.UiText;
    private static readonly Color Danger = LauncherTheme.Danger;
    private static readonly Regex ProgressPattern = new(@"\]\s*(?<percent>\d{1,3})%\s+(?<message>.+)$", RegexOptions.Compiled);

    private readonly string _factoryRoot;
    private readonly string _launcherPath;
    private readonly string _runtimeStatePath;
    private readonly string _runtimeLogRoot;
    private readonly HttpClient _http = new() { Timeout = TimeSpan.FromSeconds(2) };
    private readonly System.Windows.Forms.Timer _monitor = new() { Interval = 1200 };
    private readonly Label _statusLabel = new();
    private readonly Label _modelSummary = new();
    private readonly Panel _progressTrack = new();
    private readonly Panel _progressFill = new();
    private readonly StageView[] _stages;
    private readonly TextBox _logBox = new();
    private readonly ThemedButton _openButton = new();
    private readonly ThemedButton _modelButton = new();
    private readonly ThemedButton _restartButton = new();
    private readonly ThemedButton _stopButton = new();

    private Process? _ownerCommand;
    private string? _logPath;
    private long _logPosition;
    private bool _starting;
    private bool _monitoring;
    private bool _ownsStartedService;
    private bool _allowClose;
    private int _progress;

    public LauncherForm(string factoryRoot)
    {
        _factoryRoot = factoryRoot;
        _launcherPath = Path.Combine(factoryRoot, "Start-ZwVoice.cmd");
        _runtimeStatePath = Path.Combine(factoryRoot, "outputs", "runtime", "launcher.json");
        _runtimeLogRoot = Path.Combine(factoryRoot, "outputs", "logs", "runtime");

        base.Text = "ZW 语音工厂";
        StartPosition = FormStartPosition.CenterScreen;
        MinimumSize = new Size(690, 520);
        Size = new Size(760, 590);
        BackColor = Background;
        ForeColor = UiText;
        Font = new Font("Microsoft YaHei UI", 9F);
        FormBorderStyle = FormBorderStyle.Sizable;
        MaximizeBox = false;

        var title = new Label
        {
            AutoSize = true,
            Location = new Point(28, 24),
            Text = "ZW 语音工厂",
            Font = new Font("Microsoft YaHei UI", 18F, FontStyle.Bold),
            ForeColor = Primary,
        };
        Controls.Add(title);

        var subtitle = new Label
        {
            AutoSize = true,
            Location = new Point(31, 60),
            Text = "本地多角色语音生产控制台",
            ForeColor = Muted,
        };
        Controls.Add(subtitle);

        _statusLabel.AutoSize = true;
        _statusLabel.Anchor = AnchorStyles.Top | AnchorStyles.Right;
        _statusLabel.ForeColor = Muted;
        _statusLabel.Text = "正在检查运行状态";
        _statusLabel.TextAlign = ContentAlignment.MiddleRight;
        Controls.Add(_statusLabel);

        _modelButton.Text = "模型管理";
        _modelButton.BackColor = Surface;
        _modelButton.ForeColor = UiText;
        _modelButton.Size = new Size(104, 34);
        _modelButton.Anchor = AnchorStyles.Top | AnchorStyles.Right;
        StyleButton(_modelButton);
        _modelButton.Click += (_, _) => OpenModelManager();
        Controls.Add(_modelButton);

        _progressTrack.Location = new Point(30, 102);
        _progressTrack.Size = new Size(684, 9);
        _progressTrack.BackColor = LauncherTheme.Track;
        _progressTrack.Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right;
        _progressTrack.SizeChanged += (_, _) => UpdateProgress(_progress);
        _progressFill.Dock = DockStyle.Left;
        _progressFill.Width = 0;
        _progressFill.BackColor = Primary;
        _progressTrack.Controls.Add(_progressFill);
        Controls.Add(_progressTrack);

        _stages = new[]
        {
            new StageView("运行环境"),
            new StageView("项目后端"),
            new StageView("语音模型"),
            new StageView("服务健康"),
        };
        for (var index = 0; index < _stages.Length; index++)
        {
            var stage = _stages[index];
            stage.Anchor = AnchorStyles.Top | AnchorStyles.Left;
            stage.Location = new Point(30 + index * 175, 130);
            Controls.Add(stage);
        }

        _modelSummary.AutoSize = false;
        _modelSummary.Location = new Point(30, 178);
        _modelSummary.Size = new Size(684, 22);
        _modelSummary.Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right;
        _modelSummary.ForeColor = Muted;
        Controls.Add(_modelSummary);

        _logBox.Location = new Point(30, 208);
        _logBox.Size = new Size(684, 255);
        _logBox.Anchor = AnchorStyles.Top | AnchorStyles.Bottom | AnchorStyles.Left | AnchorStyles.Right;
        _logBox.Multiline = true;
        _logBox.ReadOnly = true;
        _logBox.ScrollBars = ScrollBars.Vertical;
        _logBox.BackColor = Surface;
        _logBox.ForeColor = LauncherTheme.LogText;
        _logBox.BorderStyle = BorderStyle.FixedSingle;
        _logBox.Font = new Font("Cascadia Mono", 9F);
        _logBox.WordWrap = false;
        Controls.Add(_logBox);

        _openButton.Text = "打开语音工厂";
        _openButton.BackColor = Primary;
        _openButton.ForeColor = Background;
        _openButton.Enabled = false;
        _openButton.Location = new Point(30, 486);
        _openButton.Size = new Size(144, 38);
        _openButton.Anchor = AnchorStyles.Bottom | AnchorStyles.Left;
        StyleButton(_openButton);
        _openButton.FlatAppearance.MouseOverBackColor = LauncherTheme.PrimaryHover;
        _openButton.Click += (_, _) => OpenWorkspace();
        _openButton.EnabledChanged += (_, _) => UpdateButtonAppearance();
        Controls.Add(_openButton);

        _restartButton.Text = "重新启动服务";
        _restartButton.Location = new Point(435, 486);
        _restartButton.Size = new Size(130, 38);
        _restartButton.Anchor = AnchorStyles.Bottom | AnchorStyles.Right;
        StyleButton(_restartButton);
        _restartButton.Click += async (_, _) => await RestartAsync();
        _restartButton.EnabledChanged += (_, _) => UpdateButtonAppearance();
        Controls.Add(_restartButton);

        _stopButton.Text = "停止并退出";
        _stopButton.Location = new Point(580, 486);
        _stopButton.Size = new Size(134, 38);
        _stopButton.Anchor = AnchorStyles.Bottom | AnchorStyles.Right;
        _stopButton.ForeColor = Danger;
        StyleButton(_stopButton);
        _stopButton.Click += async (_, _) => await StopAndCloseAsync();
        _stopButton.EnabledChanged += (_, _) => UpdateButtonAppearance();
        Controls.Add(_stopButton);

        Resize += (_, _) => LayoutControls();
        Shown += async (_, _) => await InitializeAsync();
        FormClosing += OnFormClosing;
        _monitor.Tick += async (_, _) => await MonitorAsync();
        UpdateButtonAppearance();
        AppendLog("正在检查 ZW 语音工厂的现有服务。");
        RefreshModelSummary();
    }

    private static void StyleButton(Button button)
    {
        button.FlatStyle = FlatStyle.Flat;
        button.UseVisualStyleBackColor = false;
        button.FlatAppearance.BorderColor = LauncherTheme.Border;
        button.FlatAppearance.MouseOverBackColor = LauncherTheme.AccentSoft;
        button.Font = new Font("Microsoft YaHei UI", 9F, FontStyle.Regular);
        button.Cursor = Cursors.Hand;
    }

    private void UpdateButtonAppearance()
    {
        _openButton.BackColor = _openButton.Enabled ? Primary : LauncherTheme.DisabledSurface;
        _openButton.ForeColor = _openButton.Enabled ? LauncherTheme.PrimaryText : LauncherTheme.DisabledText;
        _restartButton.BackColor = Surface;
        _restartButton.ForeColor = _restartButton.Enabled ? UiText : LauncherTheme.DisabledText;
        _stopButton.BackColor = Surface;
        _stopButton.ForeColor = _stopButton.Enabled ? Danger : LauncherTheme.DisabledText;
    }

    private void LayoutControls()
    {
        _statusLabel.Location = new Point(ClientSize.Width - _statusLabel.Width - 30, 30);
        _statusLabel.BringToFront();
        _modelButton.Location = new Point(_statusLabel.Left - _modelButton.Width - 14, 22);
        _modelButton.BringToFront();

        var logWidth = Math.Max(1, ClientSize.Width - 60);
        _progressTrack.Width = logWidth;
        var stageWidth = Math.Max(120, logWidth / _stages.Length);
        for (var index = 0; index < _stages.Length; index++)
        {
            _stages[index].Location = new Point(30 + index * stageWidth, 130);
            _stages[index].Width = stageWidth - 8;
        }
        _logBox.Width = logWidth;
        _modelSummary.Width = logWidth;
        _logBox.Height = Math.Max(150, ClientSize.Height - 322);
        var bottomY = ClientSize.Height - 74;
        _openButton.Location = new Point(30, bottomY);
        _stopButton.Location = new Point(ClientSize.Width - _stopButton.Width - 30, bottomY);
        _restartButton.Location = new Point(_stopButton.Left - _restartButton.Width - 15, bottomY);
    }

    private async Task InitializeAsync()
    {
        LayoutControls();
        SetStage(0, "检查中");
        await StartOrAttachAsync();
        _monitor.Start();
    }

    private async Task StartOrAttachAsync()
    {
        SetButtons(false);
        var state = ReadRuntimeState();
        var ready = await IsHealthyAsync();
        if (ready)
        {
            _ownsStartedService = state.GuiOwnerPid == Environment.ProcessId;
            AppendLog(_ownsStartedService ? "已连接到本图形启动器托管的服务。" : "已连接到现有启动器托管的服务。");
            SetReady();
            return;
        }

        if (state.LauncherPid > 0)
        {
            _ownsStartedService = state.GuiOwnerPid == Environment.ProcessId;
            _starting = true;
            AppendLog("检测到服务正在启动，正在接收启动进度。");
            SetStatus("服务启动中");
            return;
        }

        _ownsStartedService = true;
        _starting = true;
        SetStatus("正在启动服务");
        SetStage(0, "启动中");
        AppendLog("正在通过原有启动器加载模型与 WebUI。");
        try
        {
            _ownerCommand = StartGuiOwnerCommand();
            _ownerCommand.EnableRaisingEvents = true;
            _ownerCommand.Exited += (_, _) => BeginInvoke(() =>
            {
                if (_starting && !IsDisposed)
                {
                    AppendLog($"启动器进程已退出，退出码：{_ownerCommand.ExitCode}。");
                }
            });
        }
        catch (Exception exception)
        {
            SetFailed($"无法启动原有服务控制器：{exception.Message}");
        }
    }

    private Process StartGuiOwnerCommand()
    {
        var startInfo = new ProcessStartInfo
        {
            FileName = Environment.GetEnvironmentVariable("ComSpec") ?? "cmd.exe",
            WorkingDirectory = _factoryRoot,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8,
        };
        startInfo.Arguments = $"/d /s /c call \"{_launcherPath}\" gui-run {Environment.ProcessId}";

        var process = new Process { StartInfo = startInfo };
        process.OutputDataReceived += (_, eventArgs) =>
        {
            if (!string.IsNullOrWhiteSpace(eventArgs.Data))
            {
                BeginInvoke(() => AppendLog(eventArgs.Data));
            }
        };
        process.ErrorDataReceived += (_, eventArgs) =>
        {
            if (!string.IsNullOrWhiteSpace(eventArgs.Data))
            {
                BeginInvoke(() => AppendLog($"[启动器] {eventArgs.Data}"));
            }
        };
        if (!process.Start())
        {
            throw new InvalidOperationException("无法创建服务启动进程。");
        }
        process.BeginOutputReadLine();
        process.BeginErrorReadLine();
        return process;
    }

    private async Task MonitorAsync()
    {
        if (_monitoring || IsDisposed)
        {
            return;
        }
        _monitoring = true;
        try
        {
            if (_ownerCommand is null)
            {
                TailLauncherLog();
            }
            var ready = await IsHealthyAsync();
            if (ready)
            {
                SetReady();
            }
            else if (_ownerCommand is { HasExited: true } && _ownsStartedService && _starting)
            {
                SetFailed("启动器未完成服务初始化，请检查上方日志。 ");
            }
        }
        catch (Exception exception)
        {
            AppendLog($"[状态检查] {exception.Message}");
        }
        finally
        {
            _monitoring = false;
        }
    }

    private async Task<bool> IsHealthyAsync()
    {
        try
        {
            var backendTask = _http.GetAsync("http://127.0.0.1:8800/api/health");
            var serviceTasks = new[]
            {
                _http.GetAsync("http://127.0.0.1:5173/"),
                _http.GetAsync("http://127.0.0.1:9880/openapi.json"),
                _http.GetAsync("http://127.0.0.1:9881/health"),
                _http.GetAsync("http://127.0.0.1:9882/health"),
                _http.GetAsync("http://127.0.0.1:9883/health"),
                _http.GetAsync("http://127.0.0.1:11435/api/tags"),
            };
            await Task.WhenAll(serviceTasks.Prepend(backendTask));
            using var backend = await backendTask;
            if (!backend.IsSuccessStatusCode || serviceTasks.Any(task => !task.Result.IsSuccessStatusCode))
            {
                return false;
            }
            using var document = JsonDocument.Parse(await backend.Content.ReadAsStringAsync());
            return document.RootElement.TryGetProperty("launcher_managed", out var managed) && managed.ValueKind == JsonValueKind.True;
        }
        catch
        {
            return false;
        }
    }

    private void TailLauncherLog()
    {
        if (!Directory.Exists(_runtimeLogRoot))
        {
            return;
        }
        var newest = new DirectoryInfo(_runtimeLogRoot)
            .GetFiles("launcher-*.log")
            .OrderByDescending(file => file.LastWriteTimeUtc)
            .FirstOrDefault();
        if (newest is null)
        {
            return;
        }
        if (!string.Equals(_logPath, newest.FullName, StringComparison.OrdinalIgnoreCase))
        {
            _logPath = newest.FullName;
            _logPosition = 0;
            AppendLog($"正在读取启动日志：{newest.Name}");
        }

        try
        {
            using var stream = new FileStream(_logPath!, FileMode.Open, FileAccess.Read, FileShare.ReadWrite);
            if (_logPosition > stream.Length)
            {
                _logPosition = 0;
            }
            stream.Seek(_logPosition, SeekOrigin.Begin);
            using var reader = new StreamReader(stream, Encoding.UTF8, true, 4096, leaveOpen: true);
            string? line;
            while ((line = reader.ReadLine()) is not null)
            {
                AppendLog(line);
            }
            _logPosition = stream.Position;
        }
        catch (IOException)
        {
            // The PowerShell transcript may hold the file briefly while appending.
        }
    }

    private void AppendLog(string message)
    {
        if (IsDisposed)
        {
            return;
        }
        var trimmed = message.Trim();
        if (string.IsNullOrWhiteSpace(trimmed))
        {
            return;
        }
        ApplyProgress(trimmed);
        if (IsRoutineLog(trimmed))
        {
            return;
        }
        var line = $"[{DateTime.Now:HH:mm:ss}] {trimmed}";
        _logBox.AppendText(line + Environment.NewLine);
        _logBox.SelectionStart = _logBox.TextLength;
        _logBox.ScrollToCaret();
    }

    private static bool IsRoutineLog(string message)
    {
        if (
            message.Contains("GET /health", StringComparison.OrdinalIgnoreCase)
            || message.Contains("GET /openapi.json", StringComparison.OrdinalIgnoreCase)
            || message.Contains("GET /api/tags", StringComparison.OrdinalIgnoreCase)
            || message.Contains("GET /api/ps", StringComparison.OrdinalIgnoreCase)
            || message.Contains("GET /api/runtime", StringComparison.OrdinalIgnoreCase)
        )
        {
            return true;
        }
        if (
            message.StartsWith("[GIN]", StringComparison.Ordinal)
            || message.StartsWith("slot ", StringComparison.OrdinalIgnoreCase)
            || message.StartsWith("srv ", StringComparison.OrdinalIgnoreCase)
            || message.StartsWith("time=", StringComparison.OrdinalIgnoreCase) && message.Contains(" level=INFO", StringComparison.OrdinalIgnoreCase)
            || message.StartsWith("compat tensor", StringComparison.OrdinalIgnoreCase)
            || message.StartsWith("get_dummy_batch", StringComparison.OrdinalIgnoreCase)
            || message.StartsWith("reserve_compute_meta", StringComparison.OrdinalIgnoreCase)
            || message.StartsWith("warmup:", StringComparison.OrdinalIgnoreCase)
        )
        {
            return true;
        }
        return message.StartsWith("PS>TerminatingError(Invoke-WebRequest)", StringComparison.Ordinal)
            && (message.Contains("无法连接到远程服务器", StringComparison.Ordinal) || message.Contains("operation has timed out", StringComparison.OrdinalIgnoreCase));
    }

    private void ApplyProgress(string message)
    {
        var match = ProgressPattern.Match(message);
        if (!match.Success || !int.TryParse(match.Groups["percent"].Value, out var percent))
        {
            return;
        }
        UpdateProgress(Math.Clamp(percent, 0, 100));
        var detail = match.Groups["message"].Value.Trim();
        SetStatus(detail);
        if (percent >= 15) SetStage(0, "就绪");
        if (percent is >= 20 and < 28) SetStage(1, "启动中");
        if (percent >= 28) SetStage(1, "就绪");
        if (percent is >= 35 and < 94) SetStage(2, "加载中");
        if (percent >= 94) SetStage(2, "就绪");
        if (percent is >= 95 and < 100) SetStage(3, "检查中");
    }

    private void SetReady()
    {
        _starting = false;
        UpdateProgress(100);
        SetStatus("全部服务已就绪");
        for (var index = 0; index < _stages.Length; index++)
        {
            SetStage(index, "就绪");
        }
        _openButton.Enabled = true;
        _restartButton.Enabled = true;
        _stopButton.Enabled = true;
    }

    private void SetFailed(string message)
    {
        _starting = false;
        SetStatus("启动异常");
        SetStage(3, "异常");
        SetButtons(true);
        _openButton.Enabled = false;
        AppendLog($"[失败] {message}");
    }

    private void SetButtons(bool enabled)
    {
        _restartButton.Enabled = enabled;
        _stopButton.Enabled = true;
        UpdateButtonAppearance();
    }

    private void SetStatus(string text)
    {
        _statusLabel.Text = text;
        _statusLabel.Location = new Point(ClientSize.Width - _statusLabel.Width - 30, 30);
    }

    private void UpdateProgress(int percent)
    {
        _progress = percent;
        var track = _progressFill.Parent;
        if (track is null)
        {
            return;
        }
        _progressFill.Width = (int)Math.Round(track.ClientSize.Width * percent / 100d);
    }

    private void SetStage(int index, string state)
    {
        _stages[index].SetState(state);
    }

    private async Task RestartAsync()
    {
        SetButtons(false);
        _openButton.Enabled = false;
        SetStatus("正在重新启动服务");
        AppendLog("正在停止当前托管服务。");
        var stopped = await RunLauncherCommandAsync("stop");
        if (!stopped.Success)
        {
            SetFailed("未能停止当前服务，请检查启动器日志。 ");
            return;
        }
        ResetStages();
        await StartOrAttachAsync();
    }

    private async Task StopAndCloseAsync()
    {
        _restartButton.Enabled = false;
        _stopButton.Enabled = false;
        SetStatus("正在停止服务");
        AppendLog("正在关闭全部托管服务。");
        await RunLauncherCommandAsync("stop");
        _allowClose = true;
        Close();
    }

    private async Task<CommandResult> RunLauncherCommandAsync(string mode)
    {
        try
        {
            var startInfo = new ProcessStartInfo
            {
                FileName = Environment.GetEnvironmentVariable("ComSpec") ?? "cmd.exe",
                WorkingDirectory = _factoryRoot,
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                StandardOutputEncoding = Encoding.UTF8,
                StandardErrorEncoding = Encoding.UTF8,
            };
            startInfo.Arguments = $"/d /s /c call \"{_launcherPath}\" {mode}";
            using var process = Process.Start(startInfo) ?? throw new InvalidOperationException("无法启动服务控制命令。");
            var outputTask = process.StandardOutput.ReadToEndAsync();
            var errorTask = process.StandardError.ReadToEndAsync();
            await process.WaitForExitAsync();
            var output = (await outputTask) + (await errorTask);
            foreach (var line in output.Split(new[] { "\r\n", "\n" }, StringSplitOptions.RemoveEmptyEntries))
            {
                AppendLog(line);
            }
            return new CommandResult(process.ExitCode == 0, output);
        }
        catch (Exception exception)
        {
            AppendLog($"[失败] 无法执行服务控制命令：{exception.Message}");
            return new CommandResult(false, exception.Message);
        }
    }

    private void OpenWorkspace()
    {
        try
        {
            Process.Start(new ProcessStartInfo
            {
                FileName = "http://127.0.0.1:5173/",
                UseShellExecute = true,
            });
        }
        catch (Exception exception)
        {
            AppendLog($"[失败] 无法打开语音工厂：{exception.Message}");
        }
    }

    private void OpenModelManager()
    {
        using var form = new ModelManagerForm(_factoryRoot);
        form.ShowDialog(this);
        RefreshModelSummary();
    }

    private void RefreshModelSummary()
    {
        try
        {
            var entries = new ModelCatalogService(_factoryRoot).LoadEntries();
            var installed = entries.Count(entry => entry.Status == ModelInstallStatus.Installed);
            var available = entries.Count(entry => entry.HasDownloadSource && entry.Status != ModelInstallStatus.Installed);
            _modelSummary.Text = $"模型资源：{installed}/{entries.Count} 已就绪    可下载：{available}    点击右上角“模型管理”安装或配置地址";
        }
        catch (Exception exception)
        {
            _modelSummary.Text = $"模型清单不可用：{exception.Message}";
            _modelSummary.ForeColor = Danger;
        }
    }

    private void ResetStages()
    {
        _progress = 0;
        UpdateProgress(0);
        for (var index = 0; index < _stages.Length; index++)
        {
            SetStage(index, "等待中");
        }
    }

    private void OnFormClosing(object? sender, FormClosingEventArgs eventArgs)
    {
        if (_allowClose || !_ownsStartedService)
        {
            return;
        }
        eventArgs.Cancel = true;
        _ = StopOwnedServiceAndCloseAsync();
    }

    private async Task StopOwnedServiceAndCloseAsync()
    {
        if (_allowClose)
        {
            return;
        }
        _monitor.Stop();
        SetStatus("正在停止服务");
        AppendLog("正在关闭本图形启动器托管的服务。");
        await RunLauncherCommandAsync("stop");
        _allowClose = true;
        Close();
    }

    private RuntimeState ReadRuntimeState()
    {
        try
        {
            if (!File.Exists(_runtimeStatePath))
            {
                return RuntimeState.Empty;
            }
            using var document = JsonDocument.Parse(File.ReadAllText(_runtimeStatePath, Encoding.UTF8));
            var root = document.RootElement;
            var launcherPid = root.TryGetProperty("launcher_pid", out var pid) && pid.TryGetInt32(out var value) ? value : 0;
            var guiOwnerPid = root.TryGetProperty("gui_owner_pid", out var owner) && owner.TryGetInt32(out var ownerValue) ? ownerValue : 0;
            return new RuntimeState(launcherPid, guiOwnerPid);
        }
        catch (IOException)
        {
            return RuntimeState.Empty;
        }
        catch (JsonException)
        {
            return RuntimeState.Empty;
        }
    }

    protected override void Dispose(bool disposing)
    {
        if (disposing)
        {
            _monitor.Dispose();
            _http.Dispose();
            _ownerCommand?.Dispose();
        }
        base.Dispose(disposing);
    }

    private readonly record struct CommandResult(bool Success, string Output);
    private readonly record struct RuntimeState(int LauncherPid, int GuiOwnerPid)
    {
        public static RuntimeState Empty => new(0, 0);
    }
}

internal static class LauncherTheme
{
    public static readonly Color Background = Color.FromArgb(15, 16, 20);
    public static readonly Color Surface = Color.FromArgb(24, 25, 31);
    public static readonly Color DisabledSurface = Color.FromArgb(35, 32, 42);
    public static readonly Color Primary = Color.FromArgb(169, 129, 212);
    public static readonly Color PrimaryHover = Color.FromArgb(180, 138, 222);
    public static readonly Color PrimaryText = Color.FromArgb(23, 21, 27);
    public static readonly Color AccentSoft = Color.FromArgb(44, 34, 55);
    public static readonly Color Border = Color.FromArgb(116, 88, 145);
    public static readonly Color Track = Color.FromArgb(39, 35, 45);
    public static readonly Color Muted = Color.FromArgb(184, 178, 194);
    public static readonly Color DisabledText = Color.FromArgb(171, 164, 181);
    public static readonly Color UiText = Color.FromArgb(238, 240, 245);
    public static readonly Color LogText = Color.FromArgb(218, 214, 225);
    public static readonly Color StageText = Color.FromArgb(226, 222, 233);
    public static readonly Color Success = Color.FromArgb(119, 196, 179);
    public static readonly Color Warning = Color.FromArgb(228, 203, 111);
    public static readonly Color Danger = Color.FromArgb(237, 119, 102);
}

internal sealed class ThemedButton : Button
{
    protected override void OnPaint(PaintEventArgs eventArgs)
    {
        if (Enabled)
        {
            base.OnPaint(eventArgs);
            return;
        }

        eventArgs.Graphics.Clear(BackColor);
        var borderBounds = new Rectangle(0, 0, Math.Max(0, Width - 1), Math.Max(0, Height - 1));
        ControlPaint.DrawBorder(eventArgs.Graphics, borderBounds, FlatAppearance.BorderColor, ButtonBorderStyle.Solid);
        TextRenderer.DrawText(
            eventArgs.Graphics,
            Text,
            Font,
            ClientRectangle,
            ForeColor,
            TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter | TextFormatFlags.SingleLine | TextFormatFlags.EndEllipsis);
    }
}

internal sealed class StageView : Panel
{
    private readonly Label _state;

    public StageView(string name)
    {
        Size = new Size(155, 46);
        BackColor = Color.Transparent;
        var title = new Label
        {
            AutoSize = true,
            Location = new Point(0, 0),
            Text = name,
            Font = new Font("Microsoft YaHei UI", 9F, FontStyle.Bold),
            ForeColor = LauncherTheme.StageText,
        };
        Controls.Add(title);
        _state = new Label
        {
            AutoSize = true,
            Location = new Point(0, 23),
            Text = "等待中",
            ForeColor = LauncherTheme.Muted,
        };
        Controls.Add(_state);
    }

    public void SetState(string state)
    {
        _state.Text = state;
        _state.ForeColor = state switch
        {
            "就绪" => LauncherTheme.Success,
            "异常" => LauncherTheme.Danger,
            "启动中" or "加载中" or "检查中" => LauncherTheme.Warning,
            _ => LauncherTheme.Muted,
        };
    }
}

internal static class NativeWindow
{
    public const int ShowNormal = 9;

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    public static extern IntPtr FindWindow(string? className, string windowName);

    [DllImport("user32.dll")]
    public static extern bool ShowWindowAsync(IntPtr handle, int command);

    [DllImport("user32.dll")]
    public static extern bool SetForegroundWindow(IntPtr handle);
}
