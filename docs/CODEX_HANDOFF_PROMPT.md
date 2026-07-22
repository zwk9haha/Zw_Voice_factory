# Codex Prompt: Continue Zw Voice Factory

你现在接手一个新的本地项目：

```text
G:\Desktop\Zw_Voice_factory
```

旧项目位于：

```text
G:\Desktop\Zw_Voice
```

旧项目是可运行的实验记录和纪念版本。除迁移脚本明确列出的模型、工具、参考音频和测试文本外，不要修改、删除或重构旧项目代码。可以阅读旧实现作为参考，但新区代码不得直接 import 旧区 Python 模块。

## 第一步：完成资源迁移

旧任务因为工作区写权限只能在旧区生成 `_factory_seed`，尚未能够写入同级的 `Zw_Voice_factory`。先检查以下文件：

```text
G:\Desktop\Zw_Voice\_factory_seed\scripts\migrate_from_legacy.ps1
G:\Desktop\Zw_Voice\_factory_seed\scripts\verify_factory.ps1
```

先运行迁移计划，不带 `-Execute`：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File 'G:\Desktop\Zw_Voice\_factory_seed\scripts\migrate_from_legacy.ps1'
```

确认新旧绝对路径、目标为空、端口 `7860/7861/18880/18881` 已关闭后，再执行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File 'G:\Desktop\Zw_Voice\_factory_seed\scripts\migrate_from_legacy.ps1' -Execute
powershell.exe -NoProfile -ExecutionPolicy Bypass -File 'G:\Desktop\Zw_Voice_factory\scripts\verify_factory.ps1'
```

迁移内容包括：

- VoxCPM2 权重与工具。
- IndexTTS2 权重、工具和隔离环境。
- GPT-SoVITS 工具、权重和隔离环境。
- EmotiVoice、Kokoro 中文和 Sherpa ONNX 轻量语音资源。
- RVC WebUI 训练/推理工具、基础模型和历史实验目录。
- 已整理的参考音频库。
- `test_txt` 下两份《斗破苍穹》文本，迁移为新区 `input`。
- 旧项目中最终导出的 RVC `.pth + .index`，复制到新区 `assets/rvc_models/legacy_import` 用于兼容性测试。

物理资源迁入新区后，旧路径必须变成指向新区的目录联接，保证旧版仍可启动。不要删除旧区其他文件。

## 产品目标

把小说 TXT 转换成可试听、可审核、可持续播放的多角色有声内容。系统必须把故事理解、角色选角、声音资产生产、逐句导演和在线渲染分开。

共同流程：

```text
小说 TXT
→ 上下文分析
→ 角色候选、证据、别名合并、误判剔除、权重分级
→ character_voice_bible.json
→ director_doc.json
→ VoxCPM2 生成角色中性标准参考音频
→ 用户逐个试听、重生成、确认或全部采用
→ 从标准参考生成情绪子体
→ 用户审核情绪子体
```

系统有两条可自由选择的渲染路线。

### 极速路线

```text
轻量本地 TTS → 可选 RVC 角色身份层 → 句子缓存 → 连续播放
```

- EmotiVoice、Kokoro、Sherpa ONNX 等成熟轻量语音负责快速发音和基础表演。
- 重要自定义角色使用 RVC 保持角色身份。
- 低权重或不确定角色复用按性别、年龄、角色类型划分的 Archetype Voice，不允许所有龙套都复用旁白。
- 播放第 N 句时并行生成 N+1/N+2，先实现句级预取，不要急于做容易产生边界伪影的分块 RVC。

### 质量路线

```text
导演句子 → 情绪映射 → GPT-SoVITS 使用角色标准参考或情绪子体 → 缓存 → 连续播放
```

- GSV 直接承担参考音色克隆。
- RVC 不是默认叠加层；只有 A/B 稳定性测试证明能减少跨句音色漂移且不损失情绪时，才为该角色启用。
- GPT-SoVITS 不应被描述成能直接理解任意导演情绪文本。导演情绪主要通过情绪参考子体、标点、切句、语速和停顿实现。

VoxCPM2 和 IndexTTS2 主要用于离线生成角色标准参考、情绪子体和 RVC 训练素材，不作为默认在线朗读后端。

暂不实现 Android、MultiTTS 或 APK 集成，但后端接口不要把未来客户端绑定死在 Web 页面。

## 两个核心文档

`character_voice_bible.json` 是角色身份的唯一来源，包含：

- 稳定 `character_id`、名称、别名。
- 角色识别证据和置信度。
- 权重与 `core/supporting/minor/uncertain` 分级。
- 性别、年龄、性格、音色标签和 Voice Design 提示。
- Archetype Voice 分配。
- 用户确认的 Canonical Reference。
- 继承 Canonical Reference 的情绪子体。
- 极速路线和质量路线的资产策略。

`director_doc.json` 只包含逐句表演：

- `segment_id/chapter_id/character_id/text`。
- 情绪、强度、语气、句前句后停顿、语速、音高和能量。
- 不复制角色画像、参考路径和模型路径。

像“想要知道异界的斗气……”被错误解析成“想要知”角色的问题，必须通过候选证据、最低置信度、别名合并和人工审核解决。被拒绝的候选保留在分析审计报告，不进入角色声线册。

## 情绪参考资产

每个角色只有一个用户确认的中性 Canonical Reference。情绪参考是其子体：

```text
角色标准参考
├── 自然
├── 温柔
├── 悲伤
├── 愤怒
├── 紧张
└── 激动
```

每个子体记录 `parent_reference_id`、情绪、强度、文本、生成后端、种子、参数、审核状态和音频路径。同一句标准文本用于直接比较身份一致性；情绪专用文本用于检查自然度。导演句子匹配不到情绪子体时回退到中性父体。

RVC 训练只能以一个已确认的 Canonical Reference 为身份锚点。VoxCPM2/IndexTTS2 基于该锚点生成的是多条训练语料，不是多个彼此独立的角色参考。训练文本优先使用可复现、覆盖中文音素与常见句式的固定语料库，LLM 只负责选择或补充。

## 技术架构

新区采用：

```text
Frontend: React + TypeScript + Vite
UI: restrained custom CSS/shadcn-style primitives + Lucide icons
Audio waveform: WaveSurfer.js
Server state: TanStack Query
Local UI state: Zustand
Backend: FastAPI
Job progress/logs: WebSocket or SSE
```

不要继续用 Gradio 作为新区主界面。初始 UI 已在 `_factory_seed/frontend` 中搭好，参考的是三栏音频工作台：左侧角色与参考资产，中间导演脚本，右侧生成队列和波形结果。先保证信息架构、组件边界和 API 契约，不要在后端链路未跑通前投入大量装饰性动画。

原始 UI 参考图已归档为：

```text
G:\Desktop\Zw_Voice_factory\docs\ui_reference.png
```

## 实施顺序

1. 完成并验证资源迁移，确认旧区目录联接有效。
2. 安装并运行 FastAPI/React 骨架，修复任何构建问题，提供本地 URL。
3. 固化 Pydantic/TypeScript 领域契约和 JSON Schema。
4. 实现 TXT 导入、项目创建和角色候选审计，不先接模型推理。
5. 接入已有小说分析逻辑，但通过新区适配器输出新的两个文档。
6. 接入 VoxCPM2 标准参考生成和用户审核状态机。
7. 接入情绪子体生成、单独重生成、单独确认和全部采用。
8. 先打通质量路线 GSV，再打通 EmotiVoice/Kokoro + RVC 极速路线。
9. 实现句级缓存、预取队列、从指定句开始播放、失败重试和耗时/资源监测。
10. 用两份《斗破苍穹》文本做端到端验收。

每个阶段都要有可运行验证和测试。不要一次性重写所有模型适配器，不要删除旧区，不要修改共享第三方模型源码，除非存在有证据的兼容性问题且修改被隔离记录。

## 当前已知资源体积

- VoxCPM2 权重约 4.62 GB。
- IndexTTS2 权重约 10.60 GB，工具约 0.20 GB。
- GPT-SoVITS 工具、环境和权重约 5.39 GB。
- RVC WebUI 约 26.56 GB，其中 `logs` 约 23.06 GB，`assets` 约 3.50 GB。
- 参考音频库约 0.10 GB。

迁移前后都核对文件数量、目标路径和目录联接。遇到现有用户改动时与其共存，不得回滚无关文件。
