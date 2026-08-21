param(
    [Parameter(Mandatory = $true)]
    [string]$OllamaPath,
    [Parameter(Mandatory = $true)]
    [string]$LogPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Continue'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$logDirectory = Split-Path -Parent $LogPath
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
$writer = [System.IO.StreamWriter]::new(
    $LogPath,
    $true,
    [System.Text.UTF8Encoding]::new($false)
)
$writer.AutoFlush = $true
$exitCode = 1

try {
    & $OllamaPath serve 2>&1 | ForEach-Object {
        $line = [string]$_
        $writer.WriteLine($line)
        $successfulProbe = ($line -match '\[GIN\]') -and ($line -match '\|\s*2\d\d\s*\|') -and ($line -match '\|\s*(?:GET|HEAD)\s+"?/api/(?:tags|ps)"?')
        if (-not $successfulProbe) {
            [Console]::Out.WriteLine($line)
        }
    }
    $exitCode = $LASTEXITCODE
} catch {
    $writer.WriteLine($_.Exception.ToString())
    [Console]::Error.WriteLine($_.Exception.Message)
} finally {
    $writer.Dispose()
}

exit $exitCode
