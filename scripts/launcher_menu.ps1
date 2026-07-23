param(
    [string]$Selection = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$Host.UI.RawUI.WindowTitle = 'Zw Voice Factory 中文启动器'

$factoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$launcherPath = Join-Path $factoryRoot 'Start-ZwVoice.cmd'

while ($true) {
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
        '1' { & $launcherPath own }
        '2' { & $launcherPath status }
        '3' { & $launcherPath stop }
        '4' { & $launcherPath test }
        default {
            Write-Host '输入无效，请选择 0、1、2、3 或 4。' -ForegroundColor Yellow
        }
    }

    Write-Host
    [void](Read-Host '按回车键返回主菜单')
}
