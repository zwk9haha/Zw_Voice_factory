# Zw Voice Factory

Zw Voice Factory 是一个面向长篇小说的本地多角色配音工作台：从文本分析、角色审核、参考音频制作，到导演文件、连续切片生产和音频试听，全部在一个受启动器管理的项目中完成。

项目目前以 Windows 本地部署为目标。模型权重、小说原文、参考音频、生产缓存和 API 凭据均不进入 Git；公开仓库只保存应用源码、配置模板、下载索引和设计文档。

## 核心能力

- 三种文本分析模式：云端 API、本地粗筛后云端精析、本地模型分析。
- 长篇按用户设定的章节数切片，已完成切片可以先进入质量渲染，后续切片在后台准备。
- 角色候选审计、别名归并、角色声线画像、导演文件和参考文本的可审核版本。
- 质量路线支持 GPT-SoVITS V1/V2/V2 Pro/V2 Pro Plus/V3/V4 与 IndexTTS2；极速路线支持轻量 TTS + RVC。
- 参考音频历史、复用、试听、真实波形、音频缓存清理和节目级响度统一。
- RVC 异步训练、训练进度、日志、试听对比，以及启动器统一管理的进程生命周期。
- 图形启动器、模型下载队列、断点续传、来源故障转移、SHA-256 校验和安全压缩包解压。

## 技术栈

| 层 | 技术 |
| --- | --- |
| 前端 | React 18、TypeScript、Vite、TanStack Query、Zustand、WaveSurfer.js、Lucide |
| 后端 | Python、FastAPI、Pydantic、Uvicorn、pytest |
| 模型工作进程 | GPT-SoVITS、VoxCPM2、IndexTTS2、RVC、Ollama |
| 启动与资源管理 | PowerShell、Windows Job Object、C# WinForms、.NET 8 |
| 音频处理 | FFmpeg、响度测量与节目级归一化 |

## 目录结构

```text
backend/                 FastAPI 路由、领域契约、分析与生产任务
frontend/                React + TypeScript 工作台
launcher/                中文图形启动器源码
model_workers/           TTS、RVC 和运行日志工作进程
scripts/                 启动、测试、构建和音频工具脚本
config/                  模型下载目录与配置模板
skills/                  本地文本分析约束与属性词规范
docs/                    架构、ADR、领域模型与发布说明
assets/                  本地参考音频和 RVC 资产，不纳入 Git
models/                  本地模型和工具，不纳入 Git
input/                   小说原文，不纳入 Git
outputs/                 项目、音频、日志和缓存，不纳入 Git
```

## 快速开始

以下命令均在仓库根目录执行。Windows PowerShell、Python 3.11+、Node.js 20+ 和 .NET 8 SDK 是基础环境；质量路线和 RVC 还需要对应的模型工具与显卡环境。

```powershell
git clone <仓库地址>
cd Zw_Voice_factory

python -m venv backend\.venv
backend\.venv\Scripts\python.exe -m pip install -r backend\requirements-dev.txt
npm.cmd --prefix frontend ci

.\scripts\setup_audio_tools.ps1
.\Start-ZwVoice.cmd setup-analyzer
```

首次部署打开 `ZwVoiceFactoryLauncher.exe`，进入“模型管理”，按需选择公开模型源下载资源。默认目录是 `config/model_catalog.json`；个人镜像和自定义下载项写入被 Git 忽略的 `config/model_catalog.local.json`。模型管理器显示来源地址，支持断点续传、重试、故障转移和校验。

## 启动、状态与测试

图形启动器和 CMD 启动器共享同一个可见的服务所有者。不要直接启动 Vite、FastAPI 或模型工作进程，这会绕过进度显示和进程清理。

```powershell
.\Start-ZwVoice.cmd run
.\Start-ZwVoice.cmd status
.\Start-ZwVoice.cmd stop
.\Start-ZwVoice.cmd test
```

`run` 会打开 WebUI `http://127.0.0.1:5173/`；启动器负责模型预加载、云端/本地分析进度、服务日志和 Windows Job Object 清理。`test` 通过同一启动器链路运行后端测试、前端生产构建和运行时端点检查。修改图形启动器源码后运行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\build_gui_launcher.ps1
```

日志位于被忽略的 `outputs/logs/`，RVC 训练日志按任务写入 `outputs/logs/rvc/`。启动器和 WebUI 都可以查看或导出诊断信息。

## 公开发布边界

仓库不携带模型权重、模型工具的虚拟环境、小说输入、音频资产、生产输出或 API 密钥。部署者需要通过模型管理器下载公开资源，并在本地配置云端 API。模型下载地址和许可信息见 `docs/model-download-sources.md`。

代码结构、领域术语、关键架构决策和当前限制见：

- `CONTEXT.md`：领域模型与术语边界。
- `docs/architecture.md`：模块关系和数据流。
- `docs/adr/`：持续生产、响度、RVC 等关键决策记录。
- `docs/PROJECT_RESUME_AND_INTERVIEW.md`：适合项目展示和面试复盘的技术说明。
- `docs/release-inventory.md`：源码统计、资源体积和发布检查清单。

## 当前限制

这是一个需要本地模型与音频工具配合的工程，不是下载后即可运行的纯前端 Demo。不同模型的显存、依赖和许可证不同；模型目录只提供来源索引，不替代上游模型的许可证。长篇分析、云端 API 并发和 RVC 训练的耗时取决于文本规模、网络、显卡和用户配置。
