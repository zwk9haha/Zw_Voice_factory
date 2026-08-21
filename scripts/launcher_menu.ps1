param(
    [string]$Selection = '',
    [switch]$PauseAfterFailure,
    [switch]$NonInteractive
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$Host.UI.RawUI.WindowTitle = 'Zw Voice Factory 中文启动器'

$factoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$launcherPath = Join-Path $factoryRoot 'Start-ZwVoice.cmd'

function Wait-ForMenuReturn {
    while ($true) {
        $answer = Read-Host '输入 q 后按回车键返回主菜单'
        if ($answer.Trim().ToLowerInvariant() -eq 'q') {
            return
        }
        Write-Host '请输入 q，再按回车键。' -ForegroundColor Yellow
    }
}

if ($PauseAfterFailure) {
    $prompt = '启动失败。按回车键关闭此窗口'
    if ($NonInteractive) {
        Write-Host $prompt
    } else {
        [void](Read-Host $prompt)
    }
    exit 0
}

while ($true) {
    $operationExitCode = 0
    Clear-Host
    Write-Host '========================================================' -ForegroundColor DarkGray
    Write-Host '             Zw Voice Factory 2.0 中文启动器' -ForegroundColor Cyan
    Write-Host '========================================================' -ForegroundColor DarkGray
    Write-Host
    Write-Host '  [1] 启动或打开 WebUI'
    Write-Host '  [2] 查看运行状态'
    Write-Host '  [3] 关闭全部托管服务'
    Write-Host '  [4] 运行完整启动测试'
    Write-Host '  [0] 退出启动器'
    Write-Host

    if ($Selection) {
        $choice = $Selection
        $Selection = ''
        Write-Host "请选择: $choice"
    } else {
        $choice = Read-Host '请选择'
    }
    if ($choice -eq '0') {
        exit 0
    }

    switch ($choice) {
        '1' {
            & $launcherPath own
            $operationExitCode = $LASTEXITCODE
        }
        '2' {
            & $launcherPath status
            $operationExitCode = $LASTEXITCODE
        }
        '3' {
            & $launcherPath stop
            $operationExitCode = $LASTEXITCODE
        }
        '4' {
            & $launcherPath test
            $operationExitCode = $LASTEXITCODE
        }
        default {
            Write-Host '输入无效，请选择 0、1、2、3 或 4。' -ForegroundColor Yellow
            $operationExitCode = 1
        }
    }

    if ($NonInteractive) {
        exit $operationExitCode
    }
    Write-Host
    Wait-ForMenuReturn
}
