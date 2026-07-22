param(
    [string]$LegacyRoot = 'G:\Desktop\Zw_Voice',
    [string]$FactoryRoot = 'G:\Desktop\Zw_Voice_factory',
    [switch]$Execute
)

$ErrorActionPreference = 'Stop'
$SeedRoot = Split-Path -Parent $PSScriptRoot
$expectedLegacy = [System.IO.Path]::GetFullPath($LegacyRoot).TrimEnd('\')
$expectedFactory = [System.IO.Path]::GetFullPath($FactoryRoot).TrimEnd('\')
$seedPath = [System.IO.Path]::GetFullPath($SeedRoot).TrimEnd('\')

if ($expectedLegacy -eq $expectedFactory) { throw 'LegacyRoot and FactoryRoot must be different.' }
if ($expectedFactory.StartsWith($expectedLegacy + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'FactoryRoot must not be nested inside the legacy project.'
}
if (-not (Test-Path -LiteralPath $expectedLegacy -PathType Container)) { throw "Legacy root not found: $expectedLegacy" }
if (-not (Test-Path -LiteralPath $expectedFactory -PathType Container)) { throw "Factory root not found: $expectedFactory" }
if (-not $seedPath.StartsWith($expectedLegacy + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Unexpected seed location: $seedPath"
}
if ([System.IO.Path]::GetPathRoot($expectedLegacy) -ne [System.IO.Path]::GetPathRoot($expectedFactory)) {
    throw 'This migration expects both workspaces on the same volume.'
}

$resources = @(
    @{ Source = 'models\tts\voxcpm2'; Destination = 'models\tts\voxcpm2'; Kind = 'VoxCPM2 weights' },
    @{ Source = 'models\tts_tools\voxcpm2'; Destination = 'models\tts_tools\voxcpm2'; Kind = 'VoxCPM2 tool' },
    @{ Source = 'models\tts\IndexTeam--IndexTTS-2'; Destination = 'models\tts\IndexTeam--IndexTTS-2'; Kind = 'IndexTTS2 weights' },
    @{ Source = 'models\tts_tools\indextts2'; Destination = 'models\tts_tools\indextts2'; Kind = 'IndexTTS2 tool and venv' },
    @{ Source = 'models\tts_tools\gpt_sovits'; Destination = 'models\tts_tools\gpt_sovits'; Kind = 'GPT-SoVITS tool, venv, and weights' },
    @{ Source = 'models\tts\emotivoice'; Destination = 'models\tts\emotivoice'; Kind = 'EmotiVoice weights' },
    @{ Source = 'models\tts_tools\emotivoice'; Destination = 'models\tts_tools\emotivoice'; Kind = 'EmotiVoice tool' },
    @{ Source = 'models\tts\hexgrad--Kokoro-82M-v1.1-zh'; Destination = 'models\tts\hexgrad--Kokoro-82M-v1.1-zh'; Kind = 'Kokoro Chinese voices' },
    @{ Source = 'models\tts_tools\kokoro'; Destination = 'models\tts_tools\kokoro'; Kind = 'Kokoro tool' },
    @{ Source = 'models\tts\sherpa-onnx'; Destination = 'models\tts\sherpa-onnx'; Kind = 'Sherpa ONNX packaged voices' },
    @{ Source = 'models\vc_tools\rvc-webui'; Destination = 'models\vc_tools\rvc-webui'; Kind = 'RVC training and inference tool' },
    @{ Source = 'assets\voice_samples'; Destination = 'assets\voice_samples'; Kind = 'Curated reference library' },
    @{ Source = 'test_txt'; Destination = 'input'; Kind = 'Novel input texts' }
)

$plan = foreach ($item in $resources) {
    $source = Join-Path $expectedLegacy $item.Source
    $destination = Join-Path $expectedFactory $item.Destination
    [pscustomobject]@{
        Kind = $item.Kind
        Source = $source
        Destination = $destination
        SourceExists = Test-Path -LiteralPath $source
        DestinationExists = Test-Path -LiteralPath $destination
    }
}

$plan | Format-Table -AutoSize
if (-not $Execute) {
    Write-Host ''
    Write-Host 'Plan only. Re-run with -Execute after checking the paths.' -ForegroundColor Yellow
    return
}

$activePorts = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $_.LocalPort -in @(7860, 7861, 18880, 18881) }
if ($activePorts) {
    $ports = ($activePorts.LocalPort | Sort-Object -Unique) -join ', '
    throw "Stop legacy WebUI/TTS workers before migration. Active ports: $ports"
}

foreach ($row in $plan) {
    if (-not $row.SourceExists) { throw "Required source not found: $($row.Source)" }
    if ($row.DestinationExists) { throw "Destination already exists: $($row.Destination)" }
}

$null = & robocopy $SeedRoot $expectedFactory /E /R:1 /W:1 `
    /XD node_modules .npm-cache dist __pycache__ .pytest_cache `
    /XF *.pyc *.tsbuildinfo backend_dev.stdout.log backend_dev.stderr.log frontend_dev.stdout.log frontend_dev.stderr.log
$robocopyExit = $LASTEXITCODE
if ($robocopyExit -gt 7) { throw "Seed copy failed with robocopy exit code $robocopyExit" }

$migrationLog = @()
foreach ($item in $resources) {
    $source = Join-Path $expectedLegacy $item.Source
    $destination = Join-Path $expectedFactory $item.Destination
    $destinationParent = Split-Path -Parent $destination
    New-Item -ItemType Directory -Path $destinationParent -Force | Out-Null
    Move-Item -LiteralPath $source -Destination $destination
    New-Item -ItemType Junction -Path $source -Target $destination | Out-Null
    $migrationLog += [pscustomobject]@{
        kind = $item.Kind
        source = $source
        destination = $destination
        legacy_link_type = (Get-Item -LiteralPath $source -Force).LinkType
    }
}

$legacyRvcRoot = Join-Path $expectedLegacy 'outputs\voice_factory'
$rvcImportRoot = Join-Path $expectedFactory 'assets\rvc_models\legacy_import'
New-Item -ItemType Directory -Path $rvcImportRoot -Force | Out-Null
$imported = 0
if (Test-Path -LiteralPath $legacyRvcRoot) {
    Get-ChildItem -LiteralPath $legacyRvcRoot -File -Recurse -Include *.pth,*.index |
        Where-Object { $_.FullName -match '\\rvc\\rvc\\' } |
        ForEach-Object {
            $relative = $_.FullName.Substring($legacyRvcRoot.Length).TrimStart('\')
            $target = Join-Path $rvcImportRoot $relative
            New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
            Copy-Item -LiteralPath $_.FullName -Destination $target -Force
            $imported++
        }
}

$report = [ordered]@{
    migrated_at = (Get-Date).ToString('o')
    legacy_root = $expectedLegacy
    factory_root = $expectedFactory
    resources = $migrationLog
    imported_final_rvc_files = $imported
}
$reportPath = Join-Path $expectedFactory 'outputs\migration_report.json'
New-Item -ItemType Directory -Path (Split-Path -Parent $reportPath) -Force | Out-Null
$report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $reportPath -Encoding UTF8

Write-Host ''
Write-Host "Migration complete: $reportPath" -ForegroundColor Green
