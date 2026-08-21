using System.IO.Compression;
using System.Net;
using System.Net.Http.Headers;
using System.Security.Cryptography;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace ZwVoiceLauncher;

internal sealed class ModelCatalogDocument
{
    [JsonPropertyName("schema_version")]
    public int SchemaVersion { get; set; } = 1;

    [JsonPropertyName("models")]
    public List<ModelCatalogItem> Models { get; set; } = new();
}

internal sealed class ModelCatalogItem
{
    [JsonPropertyName("id")]
    public string Id { get; set; } = string.Empty;

    [JsonPropertyName("name")]
    public string Name { get; set; } = string.Empty;

    [JsonPropertyName("category")]
    public string Category { get; set; } = string.Empty;

    [JsonPropertyName("description")]
    public string Description { get; set; } = string.Empty;

    [JsonPropertyName("relative_target")]
    public string RelativeTarget { get; set; } = string.Empty;

    [JsonPropertyName("install_marker")]
    public string InstallMarker { get; set; } = string.Empty;

    [JsonPropertyName("size_bytes")]
    public long SizeBytes { get; set; }

    [JsonPropertyName("sha256")]
    public string Sha256 { get; set; } = string.Empty;

    [JsonPropertyName("archive")]
    public string Archive { get; set; } = "zip";

    [JsonPropertyName("strip_top_level")]
    public bool StripTopLevel { get; set; }

    [JsonPropertyName("recommended")]
    public bool Recommended { get; set; }

    [JsonPropertyName("required")]
    public bool Required { get; set; }

    [JsonPropertyName("provider")]
    public string Provider { get; set; } = "direct";

    [JsonPropertyName("docs_url")]
    public string DocsUrl { get; set; } = string.Empty;

    [JsonPropertyName("sources")]
    public List<ModelSource> Sources { get; set; } = new();

    [JsonPropertyName("dependencies")]
    public List<string> Dependencies { get; set; } = new();

    public ModelCatalogItem Clone() => new()
    {
        Id = Id,
        Name = Name,
        Category = Category,
        Description = Description,
        RelativeTarget = RelativeTarget,
        InstallMarker = InstallMarker,
        SizeBytes = SizeBytes,
        Sha256 = Sha256,
        Archive = Archive,
        StripTopLevel = StripTopLevel,
        Recommended = Recommended,
        Required = Required,
        Provider = Provider,
        DocsUrl = DocsUrl,
        Sources = Sources.Select(source => source.Clone()).ToList(),
        Dependencies = Dependencies.ToList(),
    };
}

internal sealed class ModelSource
{
    [JsonPropertyName("label")]
    public string Label { get; set; } = string.Empty;

    [JsonPropertyName("url")]
    public string Url { get; set; } = string.Empty;

    public ModelSource Clone() => new() { Label = Label, Url = Url };
}

internal enum ModelInstallStatus
{
    Missing,
    Installed,
    Incomplete,
    Invalid,
}

internal sealed record ModelCatalogEntry(ModelCatalogItem Item, ModelInstallStatus Status, string TargetPath)
{
    public bool HasDownloadSource => Item.Sources.Any(source => Uri.TryCreate(source.Url, UriKind.Absolute, out var uri) &&
        (uri.Scheme == Uri.UriSchemeHttps || uri.Scheme == Uri.UriSchemeHttp));
}

internal sealed class ModelCatalogService
{
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        WriteIndented = true,
    };

    private readonly string _factoryRoot;
    private readonly string _catalogPath;
    private readonly string _localCatalogPath;

    public ModelCatalogService(string factoryRoot)
    {
        _factoryRoot = Path.GetFullPath(factoryRoot);
        _catalogPath = Path.Combine(_factoryRoot, "config", "model_catalog.json");
        _localCatalogPath = Path.Combine(_factoryRoot, "config", "model_catalog.local.json");
    }

    public string CatalogPath => _catalogPath;
    public string LocalCatalogPath => _localCatalogPath;

    public IReadOnlyList<ModelCatalogEntry> LoadEntries()
    {
        var items = new Dictionary<string, ModelCatalogItem>(StringComparer.OrdinalIgnoreCase);
        foreach (var item in ReadDocument(_catalogPath).Models)
        {
            ValidateItem(item);
            items[item.Id] = item;
        }
        if (File.Exists(_localCatalogPath))
        {
            foreach (var item in ReadDocument(_localCatalogPath).Models)
            {
                ValidateItem(item);
                items[item.Id] = item;
            }
        }

        return items.Values
            .OrderBy(item => item.Category, StringComparer.OrdinalIgnoreCase)
            .ThenBy(item => item.Name, StringComparer.OrdinalIgnoreCase)
            .Select(item =>
            {
                var targetPath = ResolveRelativePath(item.RelativeTarget);
                return new ModelCatalogEntry(item, DetectStatus(item, targetPath), targetPath);
            })
            .ToList();
    }

    public ModelInstallStatus DetectStatus(ModelCatalogItem item)
    {
        return DetectStatus(item, ResolveRelativePath(item.RelativeTarget));
    }

    public void UpsertLocalItem(ModelCatalogItem item)
    {
        ValidateItem(item);
        var document = File.Exists(_localCatalogPath)
            ? ReadDocument(_localCatalogPath)
            : new ModelCatalogDocument();
        var existing = document.Models.FindIndex(candidate => string.Equals(candidate.Id, item.Id, StringComparison.OrdinalIgnoreCase));
        if (existing >= 0)
        {
            document.Models[existing] = item;
        }
        else
        {
            document.Models.Add(item);
        }
        SaveDocument(_localCatalogPath, document);
    }

    public void EnsureLocalCatalog()
    {
        if (!File.Exists(_localCatalogPath))
        {
            SaveDocument(_localCatalogPath, new ModelCatalogDocument());
        }
    }

    public string ResolveRelativePath(string relativePath)
    {
        if (string.IsNullOrWhiteSpace(relativePath) || Path.IsPathRooted(relativePath))
        {
            throw new InvalidDataException("模型目标路径必须是项目目录内的相对路径。");
        }
        var root = _factoryRoot.TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
        var fullPath = Path.GetFullPath(Path.Combine(_factoryRoot, relativePath));
        if (!fullPath.StartsWith(root, StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidDataException("模型目标路径不能跳出项目目录。");
        }
        return fullPath;
    }

    private ModelInstallStatus DetectStatus(ModelCatalogItem item, string targetPath)
    {
        if (!Directory.Exists(targetPath) && !File.Exists(targetPath))
        {
            return ModelInstallStatus.Missing;
        }
        if (string.Equals(item.Archive, "file", StringComparison.OrdinalIgnoreCase))
        {
            return File.Exists(targetPath) ? ModelInstallStatus.Installed : ModelInstallStatus.Incomplete;
        }
        if (string.IsNullOrWhiteSpace(item.InstallMarker))
        {
            return ModelInstallStatus.Installed;
        }
        try
        {
            var markerPath = Path.GetFullPath(Path.Combine(targetPath, item.InstallMarker));
            var targetRoot = targetPath.TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
            if (!markerPath.StartsWith(targetRoot, StringComparison.OrdinalIgnoreCase))
            {
                return ModelInstallStatus.Invalid;
            }
            return File.Exists(markerPath) || Directory.Exists(markerPath)
                ? ModelInstallStatus.Installed
                : ModelInstallStatus.Incomplete;
        }
        catch (Exception) when (item.InstallMarker.Length > 0)
        {
            return ModelInstallStatus.Invalid;
        }
    }

    private static ModelCatalogDocument ReadDocument(string path)
    {
        if (!File.Exists(path))
        {
            return new ModelCatalogDocument();
        }
        var document = JsonSerializer.Deserialize<ModelCatalogDocument>(File.ReadAllText(path), JsonOptions);
        return document ?? new ModelCatalogDocument();
    }

    private static void SaveDocument(string path, ModelCatalogDocument document)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        var temporaryPath = path + ".tmp";
        File.WriteAllText(temporaryPath, JsonSerializer.Serialize(document, JsonOptions));
        File.Move(temporaryPath, path, true);
    }

    private static void ValidateItem(ModelCatalogItem item)
    {
        if (!RegexHelpers.IsSafeId(item.Id))
        {
            throw new InvalidDataException($"模型 ID 无效：{item.Id}");
        }
        if (string.IsNullOrWhiteSpace(item.Name))
        {
            throw new InvalidDataException($"模型 {item.Id} 缺少名称。");
        }
        if (string.IsNullOrWhiteSpace(item.RelativeTarget))
        {
            throw new InvalidDataException($"模型 {item.Id} 缺少安装目录。");
        }
        if (!string.IsNullOrWhiteSpace(item.Sha256) &&
            (!RegexHelpers.IsHex(item.Sha256) || item.Sha256.Length != 64))
        {
            throw new InvalidDataException($"模型 {item.Id} 的 SHA-256 格式无效。");
        }
    }

    private static class RegexHelpers
    {
        public static bool IsSafeId(string value) => !string.IsNullOrWhiteSpace(value) && value.All(character =>
            char.IsLetterOrDigit(character) || character is '-' or '_' or '.');

        public static bool IsHex(string value) => value.All(character =>
            character is >= '0' and <= '9' or >= 'a' and <= 'f' or >= 'A' and <= 'F');
    }
}

internal readonly record struct DownloadProgress(long BytesReceived, long? TotalBytes, string Stage)
{
    public int Percent => TotalBytes is > 0
        ? (int)Math.Clamp(BytesReceived * 100d / TotalBytes.Value, 0, 100)
        : 0;
}

internal sealed class ModelDownloadService
{
    private readonly HttpClient _httpClient = new()
    {
        Timeout = Timeout.InfiniteTimeSpan,
    };

    public async Task InstallAsync(
        ModelCatalogEntry entry,
        string factoryRoot,
        IProgress<DownloadProgress>? progress,
        CancellationToken cancellationToken)
    {
        if (!entry.HasDownloadSource)
        {
            throw new InvalidOperationException("该模型没有可用的 HTTP/HTTPS 下载地址，请先编辑本地模型清单。");
        }
        if (entry.Item.Provider.Equals("ollama", StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidOperationException("Ollama 模型需要通过 Ollama 安装命令部署，暂不使用文件下载器。");
        }

        var catalog = new ModelCatalogService(factoryRoot);
        var targetPath = catalog.ResolveRelativePath(entry.Item.RelativeTarget);
        var downloadRoot = Path.Combine(factoryRoot, "outputs", "runtime", "model-downloads");
        Directory.CreateDirectory(downloadRoot);
        var extension = string.Equals(entry.Item.Archive, "zip", StringComparison.OrdinalIgnoreCase) ? ".zip" : ".bin";
        var partPath = Path.Combine(downloadRoot, entry.Item.Id + extension + ".part");
        Exception? lastError = null;

        foreach (var source in entry.Item.Sources)
        {
            if (!Uri.TryCreate(source.Url, UriKind.Absolute, out var uri) ||
                (uri.Scheme != Uri.UriSchemeHttp && uri.Scheme != Uri.UriSchemeHttps))
            {
                continue;
            }
            for (var attempt = 1; attempt <= 3; attempt++)
            {
                try
                {
                    await DownloadSourceAsync(uri, partPath, progress, cancellationToken).ConfigureAwait(false);
                    progress?.Report(new DownloadProgress(0, null, "正在校验 SHA-256"));
                    if (!string.IsNullOrWhiteSpace(entry.Item.Sha256))
                    {
                        var actualHash = await ComputeSha256Async(partPath, cancellationToken).ConfigureAwait(false);
                        if (!string.Equals(actualHash, entry.Item.Sha256, StringComparison.OrdinalIgnoreCase))
                        {
                            File.Delete(partPath);
                            throw new InvalidDataException($"SHA-256 校验失败：{actualHash}");
                        }
                    }
                    await InstallArchiveAsync(entry.Item, partPath, targetPath, cancellationToken).ConfigureAwait(false);
                    File.Delete(partPath);
                    progress?.Report(new DownloadProgress(1, 1, "安装完成"));
                    return;
                }
                catch (OperationCanceledException)
                {
                    throw;
                }
                catch (Exception exception)
                {
                    lastError = exception;
                    if (attempt < 3)
                    {
                        progress?.Report(new DownloadProgress(0, null, $"下载源失败，准备第 {attempt + 1} 次重试"));
                        await Task.Delay(TimeSpan.FromSeconds(attempt * 2), cancellationToken).ConfigureAwait(false);
                    }
                    else
                    {
                        progress?.Report(new DownloadProgress(0, null, $"下载源失败：{source.LabelOrUrl()}"));
                    }
                }
            }
        }

        throw new InvalidOperationException(lastError?.Message ?? "所有下载源均不可用。");
    }

    private async Task DownloadSourceAsync(
        Uri uri,
        string partPath,
        IProgress<DownloadProgress>? progress,
        CancellationToken cancellationToken)
    {
        var existingBytes = File.Exists(partPath) ? new FileInfo(partPath).Length : 0;
        using var request = new HttpRequestMessage(HttpMethod.Get, uri);
        if (existingBytes > 0)
        {
            request.Headers.Range = new RangeHeaderValue(existingBytes, null);
        }
        using var response = await _httpClient.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, cancellationToken).ConfigureAwait(false);
        if (existingBytes > 0 && response.StatusCode == HttpStatusCode.OK)
        {
            existingBytes = 0;
            File.Delete(partPath);
        }
        response.EnsureSuccessStatusCode();
        var totalBytes = response.Content.Headers.ContentLength is long length
            ? length + existingBytes
            : (long?)null;
        await using var input = await response.Content.ReadAsStreamAsync(cancellationToken).ConfigureAwait(false);
        await using var output = new FileStream(
            partPath,
            existingBytes > 0 ? FileMode.Append : FileMode.Create,
            FileAccess.Write,
            FileShare.None,
            1024 * 64,
            useAsync: true);
        var buffer = new byte[1024 * 64];
        var received = existingBytes;
        int read;
        while ((read = await input.ReadAsync(buffer, cancellationToken).ConfigureAwait(false)) > 0)
        {
            await output.WriteAsync(buffer.AsMemory(0, read), cancellationToken).ConfigureAwait(false);
            received += read;
            progress?.Report(new DownloadProgress(received, totalBytes, "正在下载"));
        }
    }

    private static async Task<string> ComputeSha256Async(string path, CancellationToken cancellationToken)
    {
        await using var stream = File.OpenRead(path);
        using var hash = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
        var buffer = new byte[1024 * 64];
        int read;
        while ((read = await stream.ReadAsync(buffer, cancellationToken).ConfigureAwait(false)) > 0)
        {
            hash.AppendData(buffer, 0, read);
        }
        return Convert.ToHexString(hash.GetHashAndReset());
    }

    private static async Task InstallArchiveAsync(
        ModelCatalogItem item,
        string archivePath,
        string targetPath,
        CancellationToken cancellationToken)
    {
        cancellationToken.ThrowIfCancellationRequested();
        if (string.Equals(item.Archive, "file", StringComparison.OrdinalIgnoreCase))
        {
            Directory.CreateDirectory(Path.GetDirectoryName(targetPath)!);
            File.Move(archivePath, targetPath, true);
            return;
        }
        if (!string.Equals(item.Archive, "zip", StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidDataException($"暂不支持的模型压缩格式：{item.Archive}");
        }

        var stagingPath = targetPath.TrimEnd(Path.DirectorySeparatorChar) + $".staging-{Guid.NewGuid():N}";
        Directory.CreateDirectory(stagingPath);
        try
        {
            using var archive = ZipFile.OpenRead(archivePath);
            foreach (var entry in archive.Entries)
            {
                cancellationToken.ThrowIfCancellationRequested();
                var destination = Path.GetFullPath(Path.Combine(stagingPath, entry.FullName));
                var stagingRoot = stagingPath.TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
                if (!destination.StartsWith(stagingRoot, StringComparison.OrdinalIgnoreCase))
                {
                    throw new InvalidDataException("压缩包包含越界路径，已拒绝安装。");
                }
                if (string.IsNullOrEmpty(entry.Name))
                {
                    Directory.CreateDirectory(destination);
                    continue;
                }
                Directory.CreateDirectory(Path.GetDirectoryName(destination)!);
                await using var source = entry.Open();
                await using var target = File.Create(destination);
                await source.CopyToAsync(target, cancellationToken).ConfigureAwait(false);
            }

            var installSource = stagingPath;
            if (item.StripTopLevel)
            {
                var directories = Directory.GetDirectories(stagingPath);
                var files = Directory.GetFiles(stagingPath);
                if (directories.Length == 1 && files.Length == 0)
                {
                    installSource = directories[0];
                }
            }
            Directory.CreateDirectory(targetPath);
            CopyDirectory(installSource, targetPath);
        }
        finally
        {
            if (Directory.Exists(stagingPath))
            {
                Directory.Delete(stagingPath, true);
            }
        }
    }

    private static void CopyDirectory(string source, string destination)
    {
        Directory.CreateDirectory(destination);
        foreach (var file in Directory.GetFiles(source))
        {
            File.Copy(file, Path.Combine(destination, Path.GetFileName(file)), true);
        }
        foreach (var directory in Directory.GetDirectories(source))
        {
            CopyDirectory(directory, Path.Combine(destination, Path.GetFileName(directory)));
        }
    }
}

internal static class ModelSourceExtensions
{
    public static string LabelOrUrl(this ModelSource source) => string.IsNullOrWhiteSpace(source.Label) ? source.Url : source.Label;
}
