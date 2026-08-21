# 发布清单

这份清单用于检查公开仓库边界。体积是 2026-08-21 在 Windows 工作区执行发布审计得到的快照，不包含 Git 对象数据库。

## 可提交内容

当前公开候选集合为 115 个文件、2,498,249 bytes（约 2.38 MiB）。其中源码与运行配置为 85 个文件、1,796,446 bytes、39,432 行；去除空行后为 36,406 行。

| 模块 | 文件 | 大小 |
| --- | ---: | ---: |
| `backend/` | 31 | 958,874 bytes |
| `frontend/` | 34 | 653,457 bytes |
| `launcher/` | 5 | 77,595 bytes |
| `scripts/` | 6 | 57,122 bytes |
| `model_workers/` | 6 | 32,785 bytes |
| `skills/` | 12 | 24,722 bytes |
| `config/` | 3 | 12,474 bytes |

核心代码按语言统计：Python 991,491 bytes / 23,551 行，TSX 339,253 bytes / 5,851 行，CSS 155,584 bytes / 1,658 行，C# 76,714 bytes / 2,025 行，TypeScript 74,724 bytes / 2,150 行，PowerShell 57,122 bytes / 1,378 行。

## 不发布内容

以下是当前工作区的本地资源体积，仅用于说明部署空间需求；这些目录由 `.gitignore` 排除，不会进入 GitHub：

| 目录 | 文件 | 大小 |
| --- | ---: | ---: |
| `models/` | 18,739 | 61,116,265,708 bytes（约 56.96 GiB） |
| `local_models/` | 10 | 3,389,986,341 bytes（约 3.16 GiB） |
| `assets/` | 493 | 932,214,568 bytes（约 889 MiB） |
| `outputs/` | 9,906 | 545,369,689 bytes（约 520 MiB） |
| `frontend/node_modules/` | 6,856 | 103,468,283 bytes（约 98.7 MiB） |
| `backend/.venv/` | 3,039 | 49,236,368 bytes（约 46.9 MiB） |
| `tools/ffmpeg/` | 3 | 162,145,033 bytes（约 154.6 MiB） |
| `input/` | 2 | 10,669,596 bytes（约 10.2 MiB） |

模型、小说和音频必须通过启动器或用户自己的本地配置准备。模型来源、直链和校验信息见 `docs/model-download-sources.md`。

## 发布前检查

```powershell
git ls-files models assets input outputs local_models
git diff --check
.\Start-ZwVoice.cmd test
```

第一条命令只能返回 `outputs/.gitkeep`。公开推送前还要确认没有 `.env`、API token、模型权重、音频和日志进入 staged 文件列表。
