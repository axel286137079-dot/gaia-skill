# 视频字幕配音 · 本地一站式流水线

把视频变成**带时间轴的中文字幕（.srt）+ AI 配音音频**——无需任何 API key，数据不出本机，纯标准库编排。

## 一句话定位

市面上的字幕工具要么收费、要么上传云端、要么锁英文；本 skill 用 **ffmpeg + OpenAI Whisper + macOS say** 三步搞定「视频 → 中文字幕 → 配音」，离线可跑、中文优先。

## 为什么值得做

- 短视频 / 知识付费 / 直播切片都需要字幕，但 whisper 的转写命令门槛不低（音频预处理、参数、输出格式）。
- 把「提取音频 → 转写 → 配音」串成一条命令，降低普通用户的使用摩擦。
- 中文是强需求：whisper 原生支持 99 种语言含中文，say 自带 Tingting 普通话语音，配合即可零成本产出中文配音。

## 快速开始

### 一键：视频 → 字幕 + 配音
```bash
python3 bin/subtitle.py all 视频.mp4 --voice Tingting
# 输出：subtitle_out/视频.srt（字幕）+ subtitle_out/视频.aiff（配音）
```

### 只转字幕
```bash
python3 bin/subtitle.py transcribe 视频.mp4 --model small --language zh
```

### 只做配音（从已有字幕）
```bash
python3 bin/subtitle.py voice 字幕.srt --voice Tingting
```

## 参数速查

| 参数 | 说明 | 默认 |
|---|---|---|
| `--model` | whisper 模型，tiny→large 越大越准越慢 | small |
| `--language` | 语言（zh 中文 / en 英文） | zh |
| `--voice` | say 语音（Tingting=普通话 / Meijia=台腔 / Sinji=粤语） | Tingting |
| `--tts` | 配音引擎（say / edge-tts） | say |

## 目录结构

```
video-subtitle/
├── SKILL.md            # 路由逻辑（触发词 + 工作流 + 参数 + 红线）
├── README.md           # 本文件
└── bin/subtitle.py     # 核心脚本（纯标准库编排，离线可跑）
```

## 依赖（脚本自动探测）

| 工具 | 用途 | 缺失时 |
|---|---|---|
| ffmpeg | 提取 16k 单声道音频 | `brew install ffmpeg` |
| whisper | 语音转文字（中文 srt） | `pip install -U openai-whisper` |
| say | macOS 配音（Tingting=普通话） | Windows/Linux 用 `--tts edge-tts` |

## 边界与红线

- whisper 转写可能有错别字（尤其口音/术语），发布前建议人工校对。
- **whisper 中文默认输出繁体字**，需简体可后处理转换，或用 `--initial_prompt "以下是简体中文"` 引导。
- 首次运行会下载模型（tiny≈72MB / small≈460MB / medium≈1.5GB / large≈2.9GB）；若报 SSL 证书错误（代理环境常见），先手动 `PYTHONHTTPSVERIFY=0 whisper 音频.wav --model small --language zh --output_format srt` 下载一次。
- say 配音是 TTS 合成音，非真人音色，商用/版权场景需自行确认合规。
- 大视频建议先用 `--model base` 快速预览，再用 `small/medium` 出精稿。

## License

MIT
