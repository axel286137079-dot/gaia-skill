---
name: cn-tts
slug: cn-tts
displayName: 中文语音合成
display_name: 中文语音合成
summary: 单条、文本文件或批量中文语音合成编排；有界等待、非空成果核验、目标冲突检测与诚实失败摘要。
description: 将授权文本交给用户选择的 edge-tts 联网引擎或 macOS say 本地引擎；支持14个预置中文音色、批量任务和1至3600秒的单次引擎超时，按实际后缀交付文件，不提供声纹克隆、音质鉴定或第三方商用许可。
display_name_en: "Chinese Speech Delivery"
description_zh: "将授权文本交给用户选择的 edge-tts 联网引擎或 macOS say 本地引擎；支持14个预置中文音色、批量任务和1至3600秒的单次引擎超时，按实际后缀交付文件，不提供声纹克隆、音质鉴定或第三方商用许可。"
description_en: "Deliver authorized Chinese text as audio from a single entry, UTF-8 file or batch; retain 14 preset edge voices, local say voices, edge rate/pitch/volume controls and a bounded 1-3600 second timeout per engine attempt. Default auto may send the text to installed online edge-tts, then fall back to macOS say; explicit say stays local, explicit edge failure does not fall back. Obtain permission before online use; no automatic install. Say keeps AIF/AIFF/WAV, redirects other suffixes to AIFF and requests WAVE/LEI16 for WAV without fixing sample rate. Validate batch structure and target conflicts, verify a new nonempty ordinary file per attempt, preserve old outputs and later batch jobs after timeout or runtime failure, and report failed items. Cooperating locks and file-identity checks are not inode-CAS, a whole-batch transaction, crash recovery, DLP or a complete local-process/path sandbox. Nonempty output is not proof of decodable audio or quality; no voice cloning or third-party service, voice or content commercial rights are granted."
author: "苏格"
version: 0.1.8
license: MIT
category: 内容创作
tags: [语音合成, 中文语音, 批量交付]
platforms: [workbuddy, claude-code, cursor]
---

# 中文语音合成

用于用户有权使用的文字配音、朗读和批量旁白。先确认文本来源、输出路径和引擎选择；联网引擎会收到正文，应得到用户授权。不生成或克隆他人声纹。

```bash
python3 bin/tts.py "你好，这是合成示例。" --engine say --voice 婷婷 --out example.wav
python3 bin/tts.py --file text.txt --voice 晓晓 --out example.mp3
python3 bin/tts.py batch examples/manifest.json --out-dir output/ --engine say
python3 bin/tts.py list-voices
```

每次引擎调用默认最多等待300秒；可用 `--timeout-seconds 1..3600` 调整。超时不会覆盖既有输出，批量模式会记录该条失败并继续后项；工具不自动重试，避免重复外发正文或失控消耗。

`auto` 默认尝试已安装的 edge-tts（需网络），失败、超时或本次无有效成果时尝试 macOS say；每个尝试分别受同一超时上限约束。显式 `--engine edge-tts` 失败不回退；`--engine say` 不调用 edge。未安装引擎时报告失败，不自动安装。音色昵称/完整名、rate/pitch/volume 参数和依赖见 [guide](references/guide.md)。

## edge-tts 版本红线（2026-10 起）

微软对 Edge 朗读接口的 `Sec-MS-GEC` 时间令牌已进入**第二代**校验，**整个 7.2 系列及更早版本自 2026-10-12 起全部 403**（含曾被当作修复版的 7.2.8）：

| 版本 | 状态 | 说明 |
|---|---|---|
| ≤ 6.1.19 | ❌ 失效 | 第一代令牌，早于 2024-10 收紧即已不可用 |
| 7.2.1 ~ 7.2.8 | ❌ 2026-10-12 起令牌轮换后全 403 | 仅实现 v1 令牌，握手必失败 |
| **7.3.0** | ✅ 首个可用 | 2026-10-13 发布，支持 `Sec-MS-GEC v2` 令牌（`<16 hex>-<64 hex>`，请求头 `Sec-MS-GEC-Version: 2-…`） |
| **7.3.1+** | ✅ 推荐 | 修 7.3.0 的时钟校准缺陷（按系统语言解析服务端 `Date` 头失败导致持续 403） |

**因此本技能要求 `edge-tts >= 7.3.1`**；`bin/tts.py` 每次调用前探测版本，低版本前置告警。`pip install --upgrade edge-tts` 即可，不需要改任何调用参数。

403 四级排障：① 先升版本（7.2.x 用户升级即修复，不必自研令牌）；② 查本机时钟偏差——令牌基于时间戳，7.3.x 已内置读服务端 `Date` 头自动校准并重试一次；③ 只有列表接口与合成接口**同时** 403 才怀疑出口 IP 受限，可换网络/`--proxy` 验证；④ 不要伪造令牌或改 `TrustedClientToken`，服务端会整体拒绝该身份。

同一时点核对：14 个预置中文音色仍全部在线（`list-voices` 返回 322 个音色），音色名无需变更。**不要**把 `pip install edge-tts` 无版本约束地写进自动化——上游轮换会让流水线整批失效而只有一句 403。

say 仅输出 AIF/AIFF/WAV，其他请求后缀改为 `.aiff`。WAV显式使用WAVE容器和LEI16 PCM，不固定采样率，AIFF路径不改。批次先校验完整结构和确定性目标冲突，再逐项执行；运行失败保留其他已交付项并返回非零。实际回退目标碰撞不能覆盖前项。不把无文件、空文件或旧文件说成本次成功。

允许显式绝对路径、`../` 和合法父目录别名；输出目录不是安全沙箱。拒绝直接覆盖本次输入/已安装材料、最终叶链接/特殊文件。普通旧文件可更新，新叶原子无覆盖，旧叶复验后替换；失败不回滚已提交文件。安全能力不足时受控拒绝。详尽并发与非CAS边界见 guide。

本包仅含 MIT 编排代码；不授予第三方引擎、音色、服务或内容的使用权。请核对其当前条款和可用性。实施者验证用本地假引擎和合成文本；主审另做本机say的WAV/AIFF合成文件对照，不播放、不作为音质、在线服务或商业许可背书。固定包的真实文件复核与独立审查仍是发布门。

公开输入/实际输出样例位于 `references/{normal,failed,malicious}-{input,output}.json`。样例产物标为 `SYNTHETIC-NOT-AUDIO`，不是可播放音频；不将其用于声音质量宣传。
