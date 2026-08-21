using System.Diagnostics;

namespace ZwVoiceLauncher;

internal sealed class ModelManagerForm : Form
{
    private readonly string _factoryRoot;
    private readonly ModelCatalogService _catalog;
    private readonly ModelDownloadService _downloader = new();
    private readonly DataGridView _modelGrid = new();
    private readonly TextBox _sourceBox = new();
    private readonly Label _statusLabel = new();
    private readonly Label _catalogLabel = new();
    private readonly ProgressBar _progressBar = new();
    private readonly ThemedButton _installButton = new();
    private readonly ThemedButton _cancelButton = new();
    private readonly ThemedButton _refreshButton = new();
    private readonly ThemedButton _addButton = new();
    private readonly ThemedButton _recommendedButton = new();
    private readonly ThemedButton _editButton = new();
    private readonly ThemedButton _openButton = new();
    private readonly List<ModelCatalogEntry> _entries = new();
    private CancellationTokenSource? _downloadCancellation;

    public ModelManagerForm(string factoryRoot)
    {
        _factoryRoot = factoryRoot;
        _catalog = new ModelCatalogService(factoryRoot);
        Text = "模型管理 - ZW 语音工厂";
        StartPosition = FormStartPosition.CenterParent;
        MinimumSize = new Size(820, 540);
        Size = new Size(980, 650);
        BackColor = LauncherTheme.Background;
        ForeColor = LauncherTheme.UiText;
        Font = new Font("Microsoft YaHei UI", 9F);

        var title = new Label
        {
            AutoSize = true,
            Location = new Point(24, 18),
            Text = "模型管理",
            Font = new Font("Microsoft YaHei UI", 17F, FontStyle.Bold),
            ForeColor = LauncherTheme.Primary,
        };
        Controls.Add(title);

        _catalogLabel.AutoSize = false;
        _catalogLabel.Location = new Point(26, 55);
        _catalogLabel.Size = new Size(720, 24);
        _catalogLabel.ForeColor = LauncherTheme.Muted;
        Controls.Add(_catalogLabel);

        var sourceTitle = new Label
        {
            AutoSize = true,
            Location = new Point(26, 118),
            Text = "下载源地址",
            ForeColor = LauncherTheme.Muted,
        };
        Controls.Add(sourceTitle);

        _sourceBox.Location = new Point(112, 114);
        _sourceBox.Size = new Size(842, 42);
        _sourceBox.Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right;
        _sourceBox.Multiline = true;
        _sourceBox.ReadOnly = true;
        _sourceBox.WordWrap = false;
        _sourceBox.ScrollBars = ScrollBars.Horizontal;
        _sourceBox.BackColor = LauncherTheme.Surface;
        _sourceBox.ForeColor = LauncherTheme.UiText;
        _sourceBox.BorderStyle = BorderStyle.FixedSingle;
        _sourceBox.Text = "选中模型后显示完整下载地址和备用源。";
        Controls.Add(_sourceBox);

        ConfigureGrid();
        Controls.Add(_modelGrid);

        _progressBar.Location = new Point(26, 505);
        _progressBar.Height = 18;
        _progressBar.Minimum = 0;
        _progressBar.Maximum = 100;
        _progressBar.Style = ProgressBarStyle.Continuous;
        _progressBar.Anchor = AnchorStyles.Bottom | AnchorStyles.Left | AnchorStyles.Right;
        Controls.Add(_progressBar);

        _statusLabel.Location = new Point(26, 532);
        _statusLabel.Size = new Size(520, 26);
        _statusLabel.Anchor = AnchorStyles.Bottom | AnchorStyles.Left;
        _statusLabel.ForeColor = LauncherTheme.Muted;
        Controls.Add(_statusLabel);

        ConfigureButton(_installButton, "安装选中", LauncherTheme.Primary);
        _installButton.Location = new Point(560, 526);
        _installButton.Size = new Size(100, 36);
        _installButton.Anchor = AnchorStyles.Bottom | AnchorStyles.Right;
        _installButton.Click += async (_, _) => await InstallSelectedAsync();
        Controls.Add(_installButton);

        ConfigureButton(_cancelButton, "取消", LauncherTheme.Surface);
        _cancelButton.Location = new Point(670, 526);
        _cancelButton.Size = new Size(82, 36);
        _cancelButton.Anchor = AnchorStyles.Bottom | AnchorStyles.Right;
        _cancelButton.Enabled = false;
        _cancelButton.Click += (_, _) => _downloadCancellation?.Cancel();
        Controls.Add(_cancelButton);

        ConfigureButton(_refreshButton, "刷新", LauncherTheme.Surface);
        _refreshButton.Location = new Point(762, 526);
        _refreshButton.Size = new Size(82, 36);
        _refreshButton.Anchor = AnchorStyles.Bottom | AnchorStyles.Right;
        _refreshButton.Click += (_, _) => RefreshEntries();
        Controls.Add(_refreshButton);

        ConfigureButton(_addButton, "添加自定义", LauncherTheme.Surface);
        _addButton.Location = new Point(854, 526);
        _addButton.Size = new Size(100, 36);
        _addButton.Anchor = AnchorStyles.Bottom | AnchorStyles.Right;
        _addButton.Click += (_, _) => AddCustomModel();
        Controls.Add(_addButton);

        _editButton.Text = "编辑本地清单";
        _editButton.BackColor = LauncherTheme.Surface;
        _editButton.ForeColor = LauncherTheme.UiText;
        _editButton.Location = new Point(26, 86);
        _editButton.Size = new Size(120, 32);
        StyleButton(_editButton);
        _editButton.Click += (_, _) => EditLocalCatalog();
        Controls.Add(_editButton);

        ConfigureButton(_openButton, "打开模型目录", LauncherTheme.Surface);
        _openButton.Location = new Point(156, 86);
        _openButton.Size = new Size(120, 32);
        _openButton.Click += (_, _) => OpenModelDirectory();
        Controls.Add(_openButton);

        ConfigureButton(_recommendedButton, "选择推荐", LauncherTheme.Surface);
        _recommendedButton.Location = new Point(700, 86);
        _recommendedButton.Size = new Size(100, 32);
        _recommendedButton.Anchor = AnchorStyles.Top | AnchorStyles.Right;
        _recommendedButton.Click += (_, _) => SelectRecommended();
        Controls.Add(_recommendedButton);

        Shown += (_, _) => RefreshEntries();
        Resize += (_, _) => LayoutBottomControls();
        FormClosing += (_, _) => _downloadCancellation?.Cancel();
        LayoutBottomControls();
    }

    private void ConfigureGrid()
    {
        _modelGrid.Location = new Point(26, 166);
        _modelGrid.Size = new Size(928, 326);
        _modelGrid.Anchor = AnchorStyles.Top | AnchorStyles.Bottom | AnchorStyles.Left | AnchorStyles.Right;
        _modelGrid.BackgroundColor = LauncherTheme.Surface;
        _modelGrid.BorderStyle = BorderStyle.FixedSingle;
        _modelGrid.GridColor = LauncherTheme.Border;
        _modelGrid.RowHeadersVisible = false;
        _modelGrid.AllowUserToAddRows = false;
        _modelGrid.AllowUserToDeleteRows = false;
        _modelGrid.AllowUserToResizeRows = false;
        _modelGrid.ReadOnly = true;
        _modelGrid.MultiSelect = true;
        _modelGrid.SelectionMode = DataGridViewSelectionMode.FullRowSelect;
        _modelGrid.AutoGenerateColumns = false;
        _modelGrid.EnableHeadersVisualStyles = false;
        _modelGrid.ColumnHeadersDefaultCellStyle = new DataGridViewCellStyle
        {
            BackColor = LauncherTheme.AccentSoft,
            ForeColor = LauncherTheme.UiText,
            SelectionBackColor = LauncherTheme.AccentSoft,
            SelectionForeColor = LauncherTheme.UiText,
        };
        _modelGrid.DefaultCellStyle = new DataGridViewCellStyle
        {
            BackColor = LauncherTheme.Surface,
            ForeColor = LauncherTheme.UiText,
            SelectionBackColor = LauncherTheme.Border,
            SelectionForeColor = Color.White,
        };
        _modelGrid.Columns.Add(new DataGridViewTextBoxColumn { HeaderText = "模型", Width = 190 });
        _modelGrid.Columns.Add(new DataGridViewTextBoxColumn { HeaderText = "类型", Width = 88 });
        _modelGrid.Columns.Add(new DataGridViewTextBoxColumn { HeaderText = "状态", Width = 96 });
        _modelGrid.Columns.Add(new DataGridViewTextBoxColumn { HeaderText = "项目内目录", Width = 270 });
        _modelGrid.Columns.Add(new DataGridViewTextBoxColumn { HeaderText = "下载源", AutoSizeMode = DataGridViewAutoSizeColumnMode.Fill });
        _modelGrid.SelectionChanged += (_, _) =>
        {
            UpdateInstallButton();
            UpdateSourceBox();
        };
        _modelGrid.CellDoubleClick += async (_, _) => await InstallSelectedAsync();
    }

    private void RefreshEntries()
    {
        try
        {
            _entries.Clear();
            _entries.AddRange(_catalog.LoadEntries());
            _modelGrid.Rows.Clear();
            foreach (var entry in _entries)
            {
                var sourceUrls = entry.Item.Sources
                    .Where(source => !string.IsNullOrWhiteSpace(source.Url))
                    .Select(source => source.Url)
                    .ToList();
                var sourceText = sourceUrls.Count > 0 ? ShortenUrl(sourceUrls[0], 48) : "未配置下载源";
                var rowIndex = _modelGrid.Rows.Add(
                    entry.Item.Name,
                    entry.Item.Category,
                    StatusText(entry),
                    entry.Item.RelativeTarget,
                    sourceText);
                _modelGrid.Rows[rowIndex].Tag = entry;
                _modelGrid.Rows[rowIndex].DefaultCellStyle.ForeColor = StatusColor(entry.Status);
            }
            _catalogLabel.Text = $"默认清单：{Path.GetFileName(_catalog.CatalogPath)}    本地覆盖：{Path.GetFileName(_catalog.LocalCatalogPath)}    共 {_entries.Count} 项";
            SetStatus("模型清单已刷新。");
            UpdateInstallButton();
            UpdateSourceBox();
        }
        catch (Exception exception)
        {
            SetStatus($"模型清单读取失败：{exception.Message}");
            MessageBox.Show(this, exception.Message, "模型清单错误", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }

    private async Task InstallSelectedAsync()
    {
        var selectedEntries = _modelGrid.SelectedRows
            .Cast<DataGridViewRow>()
            .Select(row => row.Tag as ModelCatalogEntry)
            .Where(entry => entry is not null)
            .Select(entry => entry!)
            .Where(entry => entry.Status != ModelInstallStatus.Installed)
            .DistinctBy(entry => entry.Item.Id, StringComparer.OrdinalIgnoreCase)
            .ToList();
        if (selectedEntries.Count == 0)
        {
            SetStatus("请先选择至少一个未安装模型。");
            return;
        }
        var unavailable = selectedEntries.Where(entry => !entry.HasDownloadSource).ToList();
        selectedEntries = selectedEntries.Where(entry => entry.HasDownloadSource).ToList();
        if (selectedEntries.Count == 0)
        {
            SetStatus("所选模型都没有下载地址，请先编辑本地清单。");
            return;
        }
        SetButtonsEnabled(false);
        _downloadCancellation = new CancellationTokenSource();
        _progressBar.Value = 0;
        try
        {
            var failures = new List<string>();
            for (var index = 0; index < selectedEntries.Count; index++)
            {
                var entry = selectedEntries[index];
                var progress = new Progress<DownloadProgress>(value =>
                {
                    _progressBar.Value = Math.Clamp((index * 100 + value.Percent) / selectedEntries.Count, 0, 100);
                    SetStatus(value.TotalBytes is > 0
                        ? $"[{index + 1}/{selectedEntries.Count}] {entry.Item.Name}：{value.Stage} {FormatBytes(value.BytesReceived)} / {FormatBytes(value.TotalBytes.Value)}"
                        : $"[{index + 1}/{selectedEntries.Count}] {entry.Item.Name}：{value.Stage}");
                });
                try
                {
                    await _downloader.InstallAsync(entry, _factoryRoot, progress, _downloadCancellation.Token);
                }
                catch (OperationCanceledException)
                {
                    throw;
                }
                catch (Exception exception)
                {
                    failures.Add($"{entry.Item.Name}：{exception.Message}");
                }
            }
            _progressBar.Value = 100;
            var skipped = unavailable.Count > 0 ? $"，{unavailable.Count} 项未配置地址" : string.Empty;
            SetStatus(failures.Count == 0
                ? $"已完成 {selectedEntries.Count} 项模型安装{skipped}。"
                : $"完成 {selectedEntries.Count - failures.Count}/{selectedEntries.Count} 项，{failures.Count} 项失败{skipped}。");
            if (failures.Count > 0)
            {
                MessageBox.Show(this, string.Join(Environment.NewLine, failures), "部分模型安装失败", MessageBoxButtons.OK, MessageBoxIcon.Warning);
            }
        }
        catch (OperationCanceledException)
        {
            SetStatus("下载已取消，已保留临时文件，下次可继续。");
        }
        catch (Exception exception)
        {
            SetStatus($"安装失败：{exception.Message}");
            MessageBox.Show(this, exception.Message, "模型安装失败", MessageBoxButtons.OK, MessageBoxIcon.Warning);
        }
        finally
        {
            _downloadCancellation.Dispose();
            _downloadCancellation = null;
            SetButtonsEnabled(true);
            RefreshEntries();
        }
    }

    private void AddCustomModel()
    {
        using var dialog = new CustomModelDialog(this);
        if (dialog.ShowDialog(this) != DialogResult.OK || dialog.Item is null)
        {
            return;
        }
        try
        {
            _catalog.UpsertLocalItem(dialog.Item);
            RefreshEntries();
            SetStatus($"已添加自定义模型：{dialog.Item.Name}。下载地址保存在本地清单。");
        }
        catch (Exception exception)
        {
            MessageBox.Show(this, exception.Message, "保存模型清单失败", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }

    private void EditLocalCatalog()
    {
        try
        {
            _catalog.EnsureLocalCatalog();
            Process.Start(new ProcessStartInfo
            {
                FileName = "notepad.exe",
                Arguments = $"\"{_catalog.LocalCatalogPath}\"",
                UseShellExecute = true,
            });
            SetStatus("已打开本地模型清单，编辑后点击刷新。");
        }
        catch (Exception exception)
        {
            MessageBox.Show(this, exception.Message, "无法打开本地清单", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }

    private void OpenModelDirectory()
    {
        try
        {
            var path = Path.Combine(_factoryRoot, "models");
            Directory.CreateDirectory(path);
            Process.Start(new ProcessStartInfo { FileName = path, UseShellExecute = true });
        }
        catch (Exception exception)
        {
            SetStatus($"无法打开模型目录：{exception.Message}");
        }
    }

    private void SetButtonsEnabled(bool enabled)
    {
        _installButton.Enabled = enabled;
        _refreshButton.Enabled = enabled;
        _addButton.Enabled = enabled;
        _recommendedButton.Enabled = enabled;
        _editButton.Enabled = enabled;
        _openButton.Enabled = enabled;
        _cancelButton.Enabled = !enabled;
    }

    private void UpdateInstallButton()
    {
        if (_downloadCancellation is not null)
        {
            return;
        }
        var entries = _modelGrid.SelectedRows
            .Cast<DataGridViewRow>()
            .Select(row => row.Tag as ModelCatalogEntry)
            .Where(entry => entry is not null)
            .Select(entry => entry!)
            .ToList();
        _installButton.Enabled = entries.Any(entry => entry.Status != ModelInstallStatus.Installed && entry.HasDownloadSource);
        _installButton.Text = entries.Count > 1 ? "安装所选" : "安装选中";
    }

    private void SelectRecommended()
    {
        _modelGrid.ClearSelection();
        foreach (DataGridViewRow row in _modelGrid.Rows)
        {
            if (row.Tag is ModelCatalogEntry entry && entry.Item.Recommended &&
                entry.Status != ModelInstallStatus.Installed && entry.HasDownloadSource)
            {
                row.Selected = true;
            }
        }
        UpdateInstallButton();
    }

    private void LayoutBottomControls()
    {
        _progressBar.Width = Math.Max(260, ClientSize.Width - 52);
        _statusLabel.Width = Math.Max(200, _installButton.Left - _statusLabel.Left - 12);
    }

    private void SetStatus(string value)
    {
        _statusLabel.Text = value;
    }

    private void UpdateSourceBox()
    {
        var selectedEntries = _modelGrid.SelectedRows
            .Cast<DataGridViewRow>()
            .Select(row => row.Tag as ModelCatalogEntry)
            .Where(entry => entry is not null)
            .Select(entry => entry!)
            .ToList();
        if (selectedEntries.Count == 0)
        {
            _sourceBox.Text = "选中模型后显示完整下载地址和备用源。";
            return;
        }
        var lines = selectedEntries.SelectMany(entry =>
        {
            var sources = entry.Item.Sources
                .Where(source => !string.IsNullOrWhiteSpace(source.Url))
                .Select(source => $"{entry.Item.Name} | {source.LabelOrUrl()} | {source.Url}")
                .ToList();
            return sources.Count > 0
                ? (IEnumerable<string>)sources
                : new List<string> { $"{entry.Item.Name} | 未配置 HTTP/HTTPS 下载源，请编辑本地清单。" };
        });
        _sourceBox.Text = string.Join(Environment.NewLine, lines);
        _sourceBox.SelectionStart = 0;
        _sourceBox.SelectionLength = 0;
    }

    private static string ShortenUrl(string url, int maximumLength)
    {
        if (url.Length <= maximumLength)
        {
            return url;
        }
        return url[..Math.Max(0, maximumLength - 3)] + "...";
    }

    private static string StatusText(ModelCatalogEntry entry) => entry.Status switch
    {
        ModelInstallStatus.Installed => "已安装",
        ModelInstallStatus.Incomplete => "安装不完整",
        ModelInstallStatus.Invalid => "配置错误",
        _ when !entry.HasDownloadSource => "待配置地址",
        _ => "未安装",
    };

    private static Color StatusColor(ModelInstallStatus status) => status switch
    {
        ModelInstallStatus.Installed => LauncherTheme.Success,
        ModelInstallStatus.Invalid => LauncherTheme.Danger,
        ModelInstallStatus.Incomplete => LauncherTheme.Warning,
        _ => LauncherTheme.Muted,
    };

    private static string FormatBytes(long bytes)
    {
        var units = new[] { "B", "KB", "MB", "GB", "TB" };
        var value = (double)bytes;
        var unit = 0;
        while (value >= 1024 && unit < units.Length - 1)
        {
            value /= 1024;
            unit++;
        }
        return $"{value:0.##} {units[unit]}";
    }

    private static void ConfigureButton(ThemedButton button, string text, Color background)
    {
        button.Text = text;
        button.BackColor = background;
        button.ForeColor = LauncherTheme.UiText;
        StyleButton(button);
    }

    private static void StyleButton(Button button)
    {
        button.FlatStyle = FlatStyle.Flat;
        button.UseVisualStyleBackColor = false;
        button.FlatAppearance.BorderColor = LauncherTheme.Border;
        button.FlatAppearance.MouseOverBackColor = LauncherTheme.AccentSoft;
        button.Font = new Font("Microsoft YaHei UI", 9F);
        button.Cursor = Cursors.Hand;
    }
}

internal sealed class CustomModelDialog : Form
{
    private readonly TextBox _idBox = new();
    private readonly TextBox _nameBox = new();
    private readonly TextBox _urlBox = new();
    private readonly TextBox _targetBox = new();
    private readonly TextBox _hashBox = new();
    private readonly ComboBox _archiveBox = new();

    public ModelCatalogItem? Item { get; private set; }

    public CustomModelDialog(IWin32Window owner)
    {
        Text = "添加自定义模型";
        StartPosition = FormStartPosition.CenterParent;
        ClientSize = new Size(520, 330);
        BackColor = LauncherTheme.Background;
        ForeColor = LauncherTheme.UiText;
        Font = new Font("Microsoft YaHei UI", 9F);
        FormBorderStyle = FormBorderStyle.FixedDialog;
        MaximizeBox = false;
        MinimizeBox = false;

        var layout = new TableLayoutPanel
        {
            Location = new Point(18, 16),
            Size = new Size(484, 248),
            ColumnCount = 2,
            RowCount = 6,
            Dock = DockStyle.None,
        };
        layout.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 118));
        layout.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        var labels = new[] { "模型 ID", "显示名称", "下载地址", "项目内目录", "SHA-256（可选）", "文件格式" };
        var boxes = new Control[] { _idBox, _nameBox, _urlBox, _targetBox, _hashBox, _archiveBox };
        for (var index = 0; index < labels.Length; index++)
        {
            layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 40));
            var label = new Label { Text = labels[index], Dock = DockStyle.Fill, TextAlign = ContentAlignment.MiddleLeft, ForeColor = LauncherTheme.Muted };
            layout.Controls.Add(label, 0, index);
            boxes[index].Dock = DockStyle.Fill;
            if (boxes[index] is TextBox textBox)
            {
                textBox.BackColor = LauncherTheme.Surface;
                textBox.ForeColor = LauncherTheme.UiText;
                textBox.BorderStyle = BorderStyle.FixedSingle;
            }
            layout.Controls.Add(boxes[index], 1, index);
        }
        _archiveBox.Items.AddRange(new object[] { "zip", "file" });
        _archiveBox.SelectedIndex = 0;
        _archiveBox.BackColor = LauncherTheme.Surface;
        _archiveBox.ForeColor = LauncherTheme.UiText;
        Controls.Add(layout);

        var cancel = new ThemedButton { Text = "取消", Location = new Point(306, 280), Size = new Size(92, 34), BackColor = LauncherTheme.Surface, ForeColor = LauncherTheme.UiText, DialogResult = DialogResult.Cancel };
        var save = new ThemedButton { Text = "保存", Location = new Point(410, 280), Size = new Size(92, 34), BackColor = LauncherTheme.Primary, ForeColor = LauncherTheme.PrimaryText };
        StyleButton(cancel);
        StyleButton(save);
        save.Click += (_, _) => Save();
        Controls.Add(cancel);
        Controls.Add(save);
        AcceptButton = save;
        CancelButton = cancel;
    }

    private void Save()
    {
        if (string.IsNullOrWhiteSpace(_idBox.Text) || string.IsNullOrWhiteSpace(_nameBox.Text) ||
            string.IsNullOrWhiteSpace(_urlBox.Text) || string.IsNullOrWhiteSpace(_targetBox.Text))
        {
            MessageBox.Show(this, "模型 ID、名称、下载地址和项目内目录不能为空。", "信息不完整", MessageBoxButtons.OK, MessageBoxIcon.Warning);
            return;
        }
        if (!Uri.TryCreate(_urlBox.Text.Trim(), UriKind.Absolute, out var uri) ||
            (uri.Scheme != Uri.UriSchemeHttps && uri.Scheme != Uri.UriSchemeHttp))
        {
            MessageBox.Show(this, "下载地址必须是 HTTP 或 HTTPS。", "地址无效", MessageBoxButtons.OK, MessageBoxIcon.Warning);
            return;
        }
        if (string.IsNullOrWhiteSpace(_hashBox.Text))
        {
            var result = MessageBox.Show(this, "没有填写 SHA-256，安装时无法验证文件完整性。仍然保存吗？", "缺少完整性校验", MessageBoxButtons.YesNo, MessageBoxIcon.Warning);
            if (result != DialogResult.Yes)
            {
                return;
            }
        }
        Item = new ModelCatalogItem
        {
            Id = _idBox.Text.Trim(),
            Name = _nameBox.Text.Trim(),
            Category = "自定义",
            Description = "用户自定义模型",
            RelativeTarget = _targetBox.Text.Trim(),
            InstallMarker = string.Empty,
            Sha256 = _hashBox.Text.Trim(),
            Archive = _archiveBox.SelectedItem?.ToString() ?? "zip",
            Sources = new List<ModelSource> { new() { Label = "自定义地址", Url = _urlBox.Text.Trim() } },
        };
        DialogResult = DialogResult.OK;
        Close();
    }

    private static void StyleButton(Button button)
    {
        button.FlatStyle = FlatStyle.Flat;
        button.UseVisualStyleBackColor = false;
        button.FlatAppearance.BorderColor = LauncherTheme.Border;
        button.FlatAppearance.MouseOverBackColor = LauncherTheme.AccentSoft;
        button.Cursor = Cursors.Hand;
    }
}
