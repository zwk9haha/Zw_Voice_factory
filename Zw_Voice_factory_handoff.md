# Zw Voice Factory 交接提示词

你现在接手本地新项目：

```text
G:\Desktop\Zw_Voice_factory
```

旧项目位于：

```text
G:\Desktop\Zw_Voice
```

## 当前状态

- 新区目录已经创建，但目前为空；资源迁移尚未执行。
- 完整的新区种子工程位于 `G:\Desktop\Zw_Voice\_factory_seed`。
- 种子工程已经包含 React + TypeScript + Vite 前端、FastAPI 后端契约、迁移脚本、验证脚本、架构文档和 UI 参考图。
- 已验证：后端测试 `1 passed`，Vite 生产构建通过，两份 PowerShell 脚本语法检查通过，迁移计划模式运行通过。
- 旧项目服务端口 `7861` 和 `18880` 在上次检查时仍被占用。迁移脚本会在 `7860/7861/18880/18881` 任一端口未关闭时拒绝执行。
- 不要重写或清理旧项目。旧区是可运行的实验记录和纪念版本，只允许按迁移脚本移动共享模型、工具、参考音频和测试文本，并在旧路径建立指向新区的目录联接。

## 先读这些文件

这些文件是权威上下文，不要在本提示词中另行推测或重复设计：

```text
G:\Desktop\Zw_Voice\_factory_seed\docs\CODEX_HANDOFF_PROMPT.md
G:\Desktop\Zw_Voice\_factory_seed\CONTEXT.md
G:\Desktop\Zw_Voice\_factory_seed\docs\architecture.md
G:\Desktop\Zw_Voice\_factory_seed\docs\adr\0001-dual-rendering-routes.md
G:\Desktop\Zw_Voice\_factory_seed\scripts\migrate_from_legacy.ps1
G:\Desktop\Zw_Voice\_factory_seed\scripts\verify_factory.ps1
```

UI 参考图：

```text
G:\Desktop\Zw_Voice\_factory_seed\docs\ui_reference.png
```

## 产品方向

目标是把小说 TXT 转换为可审核、可持续播放的多角色有声内容。共享生产流程是：

```text
TXT
→ 角色候选、别名、证据、置信度和权重分析
→ character_voice_bible.json
→ director_doc.json
→ VoxCPM2 生成角色中性标准参考音频
→ 用户试听、重生成和确认
→ 从标准参考派生不同情绪的子参考音频
```

最终保留两条渲染路线：

```text
极速路线：轻量本地 TTS → 可选 RVC 身份层 → 缓存/连续播放
质量路线：导演情绪映射 → GPT-SoVITS 标准/情绪参考 → 缓存/连续播放
```

关键约束：低权重角色复用按性别、年龄和角色类型划分的 Archetype Voice，不能全部复用旁白；RVC 训练以一个已确认的 Canonical Reference 为身份锚点；情绪参考是该锚点的子资产；质量路线是否叠加 RVC 必须经过 A/B 稳定性验证；Android/MultiTTS 暂缓。

## 立即执行

1. 先检查工作树和端口占用，保留用户已有改动。
2. 先运行迁移计划，不带 `-Execute`：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File 'G:\Desktop\Zw_Voice\_factory_seed\scripts\migrate_from_legacy.ps1'
```

3. 确认目标为空、路径正确，并关闭 `7860/7861/18880/18881` 对应服务后，执行迁移和验证：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File 'G:\Desktop\Zw_Voice\_factory_seed\scripts\migrate_from_legacy.ps1' -Execute
powershell.exe -NoProfile -ExecutionPolicy Bypass -File 'G:\Desktop\Zw_Voice_factory\scripts\verify_factory.ps1'
```

4. 启动并验证 FastAPI/React 骨架，修复真实构建或启动问题，向用户提供本地访问 URL。
5. 随后严格按 `CODEX_HANDOFF_PROMPT.md` 中的实施顺序推进。第一阶段先固化领域契约、TXT 导入、项目创建和角色候选审计，不要一次性重写所有模型适配器。

迁移包含 VoxCPM2、IndexTTS2、GPT-SoVITS、EmotiVoice、Kokoro、Sherpa ONNX、RVC WebUI、整理后的参考音频库、旧区最终 RVC 模型，以及 `test_txt` 中两份《斗破苍穹》文本。迁移成功后，物理资源位于新区，旧路径应成为指向新区的目录联接。

## 工程要求

- 新区主界面使用 React + TypeScript + Vite + FastAPI，不继续以 Gradio 为主界面。
- 新区代码不能直接 import 旧区 Python 模块；可阅读旧实现并通过新区适配器迁移能力。
- 不删除旧区，不回滚无关改动，不修改共享第三方模型源码，除非有可复现的兼容问题并隔离记录补丁。
- 每个阶段都需要可运行验证和测试；长任务进度及日志使用 WebSocket 或 SSE 实时反馈。
- 优先完成端到端纵向链路，再做装饰性 UI 和大范围抽象。

## Suggested Skills

- `$implement`：按现有架构和实施顺序完成迁移、骨架启动及纵向功能。
- `$domain-modeling`：维护 `character_voice_bible.json`、`director_doc.json`、Canonical Reference 和情绪子体的统一术语与契约。
- `$codebase-design`：设计模型适配器、任务队列、缓存和播放模块的清晰边界。
- `$diagnosing-bugs`：处理迁移、模型环境、启动、GPU 推理或连续播放中的失败与性能问题。
- `$tdd`：为领域契约、候选角色过滤、资产状态机和 API 添加风险匹配的测试。

完成上述第一阶段后，汇报实际迁移结果、验证结果、启动 URL、发现的问题和下一阶段工作，不要只给方案。
