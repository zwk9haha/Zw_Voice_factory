param(
    [string]$FactoryRoot = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath $FactoryRoot).Path
$marker = Join-Path $root 'AGENTS.md'
if (-not (Test-Path -LiteralPath $marker -PathType Leaf)) {
    throw "Refusing to clean an unrecognized directory: $root"
}

$preservedInputs = @(Get-ChildItem -LiteralPath (Join-Path $root 'input') -File -ErrorAction SilentlyContinue | Select-Object -ExpandProperty FullName)
$targets = @(
    (Join-Path $root '.pytest_cache'),
    (Join-Path $root 'backend\.pytest_cache'),
    (Join-Path $root 'frontend\.npm-cache'),
    (Join-Path $root 'frontend\dist'),
    (Join-Path $root 'frontend\node_modules\.vite'),
    (Join-Path $root 'launcher\publish')
)

$outputs = Join-Path $root 'outputs'
if (Test-Path -LiteralPath $outputs -PathType Container) {
    Get-ChildItem -LiteralPath $outputs -Force |
        Where-Object Name -ne '.gitkeep' |
        Remove-Item -Recurse -Force
}

foreach ($target in $targets) {
    if (-not (Test-Path -LiteralPath $target)) {
        continue
    }
    $resolved = (Resolve-Path -LiteralPath $target).Path
    if (-not $resolved.StartsWith($root + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove a path outside the project: $resolved"
    }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}

$pythonRoots = @(
    (Join-Path $root 'backend\app'),
    (Join-Path $root 'backend\tests'),
    (Join-Path $root 'model_workers')
)
foreach ($pythonRoot in $pythonRoots) {
    if (-not (Test-Path -LiteralPath $pythonRoot -PathType Container)) {
        continue
    }
    Get-ChildItem -LiteralPath $pythonRoot -Recurse -Directory -Force -ErrorAction SilentlyContinue |
        Where-Object Name -eq '__pycache__' |
        Sort-Object FullName -Descending |
        Remove-Item -Recurse -Force
    Get-ChildItem -LiteralPath $pythonRoot -Recurse -File -Force -Filter '*.pyc' -ErrorAction SilentlyContinue |
        Remove-Item -Force
}

Get-ChildItem -LiteralPath (Join-Path $root 'frontend') -File -Force -Filter '*.tsbuildinfo' -ErrorAction SilentlyContinue |
    Remove-Item -Force

Get-ChildItem -LiteralPath (Join-Path $root 'launcher') -Recurse -Directory -Force -ErrorAction SilentlyContinue |
    Where-Object Name -in @('bin', 'obj') |
    Sort-Object FullName -Descending |
    Remove-Item -Recurse -Force

$remainingInputs = @(Get-ChildItem -LiteralPath (Join-Path $root 'input') -File -ErrorAction SilentlyContinue | Select-Object -ExpandProperty FullName)
if (Compare-Object $preservedInputs $remainingInputs) {
    throw 'Input files changed during cleanup.'
}

Write-Host "Runtime state removed. Preserved $($remainingInputs.Count) input files." -ForegroundColor Green
