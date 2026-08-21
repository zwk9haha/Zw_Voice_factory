param(
    [switch]$SelfContained
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$factoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$projectPath = Join-Path $factoryRoot 'launcher\ZwVoiceLauncher\ZwVoiceLauncher.csproj'
$outputDirectory = Join-Path $factoryRoot 'launcher\publish'
$launcherPath = Join-Path $factoryRoot 'ZwVoiceFactoryLauncher.exe'
$dotnetPath = (Get-Command dotnet.exe -ErrorAction Stop).Source

$publishArguments = @(
    'publish', $projectPath,
    '--configuration', 'Release',
    '--runtime', 'win-x64',
    '--output', $outputDirectory,
    '-p:PublishSingleFile=true',
    '-p:DebugType=None',
    '-p:DebugSymbols=false'
)
if ($SelfContained) {
    $publishArguments += '--self-contained'
    $publishArguments += 'true'
} else {
    $publishArguments += '--self-contained'
    $publishArguments += 'false'
}

& $dotnetPath @publishArguments
if ($LASTEXITCODE -ne 0) {
    throw "GUI launcher build failed with exit code: $LASTEXITCODE"
}

$publishedLauncher = Join-Path $outputDirectory 'ZwVoiceFactoryLauncher.exe'
if (-not (Test-Path -LiteralPath $publishedLauncher -PathType Leaf)) {
    throw "Published GUI launcher was not found: $publishedLauncher"
}
Copy-Item -LiteralPath $publishedLauncher -Destination $launcherPath -Force
Write-Host "GUI launcher created: $launcherPath" -ForegroundColor Green
if (-not $SelfContained) {
    Write-Host 'This build requires the .NET 7 Desktop Runtime.' -ForegroundColor DarkGray
}
