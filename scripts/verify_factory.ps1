param(
    [string]$LegacyRoot = 'G:\Desktop\Zw_Voice',
    [string]$FactoryRoot = 'G:\Desktop\Zw_Voice_factory'
)

$ErrorActionPreference = 'Stop'
$checks = @(
    'models\tts\voxcpm2',
    'models\tts_tools\voxcpm2',
    'models\tts\IndexTeam--IndexTTS-2',
    'models\tts_tools\indextts2',
    'models\tts_tools\gpt_sovits',
    'models\vc_tools\rvc-webui',
    'assets\voice_samples',
    'input'
)

$rows = foreach ($relative in $checks) {
    $factoryPath = Join-Path $FactoryRoot $relative
    $legacyRelative = if ($relative -eq 'input') { 'test_txt' } else { $relative }
    $legacyPath = Join-Path $LegacyRoot $legacyRelative
    $legacyItem = Get-Item -LiteralPath $legacyPath -Force -ErrorAction SilentlyContinue
    [pscustomobject]@{
        Resource = $relative
        FactoryExists = Test-Path -LiteralPath $factoryPath
        LegacyExists = [bool]$legacyItem
        LegacyLinkType = if ($legacyItem) { $legacyItem.LinkType } else { '' }
        LegacyTarget = if ($legacyItem) { $legacyItem.Target -join ';' } else { '' }
    }
}

$rows | Format-Table -AutoSize
if ($rows.Where({ -not $_.FactoryExists -or -not $_.LegacyExists -or $_.LegacyLinkType -ne 'Junction' }).Count) {
    throw 'Factory verification failed.'
}
Write-Host 'Factory resources and legacy junctions verified.' -ForegroundColor Green
