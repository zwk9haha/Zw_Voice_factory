[CmdletBinding()]
param(
    [switch]$Force
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$workspaceRoot = Split-Path -Parent $PSScriptRoot
$toolRoot = Join-Path $workspaceRoot "tools\ffmpeg"
$binRoot = Join-Path $toolRoot "bin"
$ffmpegPath = Join-Path $binRoot "ffmpeg.exe"
$archivePath = Join-Path $toolRoot "ffmpeg-9.0.1-essentials_build.zip"
$extractRoot = Join-Path $toolRoot "extract-9.0.1"
$downloadUrl = "https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-9.0.1-essentials_build.zip"
$expectedArchiveSha256 = "FEC81AE03971D9DD4BE3EBE02E263BD2EC1D789483F931BDBA5F5715E65DA2E9"

New-Item -ItemType Directory -Path $binRoot -Force | Out-Null

if ((-not $Force) -and (Test-Path -LiteralPath $ffmpegPath -PathType Leaf)) {
    & $ffmpegPath -version | Select-Object -First 1
    Write-Host "Project-local FFmpeg is already installed: $ffmpegPath"
    exit 0
}

if (Test-Path -LiteralPath $archivePath -PathType Leaf) {
    $archiveHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash
    if ($archiveHash -ne $expectedArchiveSha256) {
        Remove-Item -LiteralPath $archivePath -Force
    }
}

if (-not (Test-Path -LiteralPath $archivePath -PathType Leaf)) {
    Write-Host "Downloading FFmpeg 9.0.1 to the project drive..."
    & curl.exe -L --fail --retry 3 --retry-delay 3 --continue-at - --output $archivePath $downloadUrl
    if ($LASTEXITCODE -ne 0) {
        throw "FFmpeg download failed with exit code $LASTEXITCODE. Run this script again to resume."
    }
}

$archiveHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash
if ($archiveHash -ne $expectedArchiveSha256) {
    throw "FFmpeg archive hash mismatch. Expected $expectedArchiveSha256, got $archiveHash."
}

if (Test-Path -LiteralPath $extractRoot) {
    Remove-Item -LiteralPath $extractRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $extractRoot -Force | Out-Null
Expand-Archive -LiteralPath $archivePath -DestinationPath $extractRoot -Force

$downloadedBin = Get-ChildItem -LiteralPath $extractRoot -Recurse -Directory |
    Where-Object { $_.Name -eq "bin" } |
    Select-Object -First 1
if ($null -eq $downloadedBin) {
    throw "The FFmpeg archive does not contain a bin directory."
}

Get-ChildItem -LiteralPath $downloadedBin.FullName -Filter "*.exe" -File |
    Copy-Item -Destination $binRoot -Force
Remove-Item -LiteralPath $extractRoot -Recurse -Force

& $ffmpegPath -version | Select-Object -First 1
Write-Host "Installed project-local FFmpeg: $ffmpegPath"
