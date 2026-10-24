# GPT-SoVITS 生成失败修复与最小流播实测

## 问题与根因

2026-10-24 的 16 个 GPT-SoVITS V2 Pro Plus 质量任务均在 15% 失败，返回：

```text
HTTP 400: {"message":"tts failed","Exception":"'function' object has no attribute 'get_call_template'"}
```

启动器原先向 GPT-SoVITS 设置 `NUMBA_DISABLE_JIT=1`。当前运行环境的 librosa 0.11.0
在懒加载 `librosa.load` 时构造 `guvectorize`/`stencil` 函数；关闭 JIT 使其依赖的函数
成为普通 Python 函数，Numba 随后无法调用 dispatcher 的 `get_call_template`。

使用模型自身 Python 环境、内存中的 24 kHz WAV 和 `librosa.load(..., sr=16000)`，
无需模型权重或实际小说即可复现完全相同的异常。第一轮回归结果为
`1 failed, 180 deselected`。因此本次确认的原因是 JIT 禁用配置；无需降级或修改共享 Anaconda 包。

## 修改

- `model_workers/gpt_sovits_server.py` 在导入音频依赖前设置 `NUMBA_DISABLE_JIT=0`，
  包括纠正继承来的禁用值，然后转交原来的 `api_v2.py` 和全部上游参数。
- 启动前真实检查 WAV 读取、重采样及 STFT。音频依赖异常会在服务可用之前暴露。
  该检查不代替模型实际合成测试。
- GPT-SoVITS 的输出现在同步保存到 `outputs/logs/runtime/gpt_sovits-*.log`。
- 启动器不再设置禁用 JIT；模型的启动、停止和清理由原启动器继续管理。
- 新增真实音频依赖回归，以及通过启动器进入的可选浏览器流播实测。

## 验证

代码检查通过前端类型检查、生产构建和现有界面契约检查；以下定向后端测试
结果为 `4 passed, 177 deselected`：

```powershell
.\Start-ZwVoice.cmd test -ChecksOnly -BackendTestFilter 'gpt_sovits_audio_runtime or quality_stream or v3_v4_stream or stream_payload'
```

代码检查要求先关闭当前服务。检查完成后，通过 `Start-ZwVoice.cmd run` 恢复项目，
随后使用 `status` 确认就绪，并确认 owner `cmd.exe` 有非零窗口句柄。

真实生成与播放通过以下入口执行，复用当前正常启动的实例：

```powershell
.\Start-ZwVoice.cmd test -QualityStreamSmoke
```

该实测需要 Playwright 和 Chromium。脚本默认解析 `playwright` 包及本机 Chrome/Edge；
外部运行环境可通过 `ZW_VOICE_PLAYWRIGHT_MODULE` 指向 Playwright 的入口文件，
通过 `ZW_VOICE_BROWSER_EXECUTABLE` 指向浏览器程序。

实测读取已有项目的参考音频和对应参考文本，使用独立的 `quality-smoke-*` 任务标识，
不修改用户导演文档、声音审核状态或全局模型选择。测试仅验证基础质量生成，关闭了测试任务的 RVC。

| 检查 | 2026-10-24 实测结果 |
| --- | --- |
| V2 Pro Plus 两句生成 | 成功，时长分别 9.68 秒、1.66 秒 |
| 下一句预取 | 第一段播放期间，第二段在约 2.19 秒内生成并就绪 |
| 浏览器逐句播放 | 两段均播放至结束；事件计时的接续间隔小于 1 毫秒 |
| V3 PCM 分段流 | 6 个音频分段，279808 字节，24 kHz，约 5.83 秒 |
| 首段／响应结束 | 请求后约 16.28 秒收到首段，约 20.58 秒完整收完，提前约 4.30 秒开始播放 |
| PCM 暂停／继续 | 成功，播放器最终正常结束 |
| 流播缓存 | 完成任务并保存可复用 WAV |

实测通过真实本地模型、HTTP 端点及浏览器播放组件完成；不是整本小说的长时间压力测试，
也不是对最终听感或样本级无缝拼接的评价。V2 Pro Plus 仍为逐句生成与预取播放；
句内 PCM 流目前仅由 V3/V4 入口提供，本轮实际验证的是 V3。

本机证据：

- 报告：`outputs/logs/diagnostics/quality-stream-smoke-2026-10-24T15-07-39-670Z.json`
- V2 两句任务：`1948dfd013ff`、`a75eacba237d`
- V3 流播任务：`d21313fd171b`
- 音频：`outputs/audio/jobs/<任务 ID>.wav`

运行报告和音频属于本机产物，不提交到 Git。源码、回归测试和本说明随开发存档保存。
