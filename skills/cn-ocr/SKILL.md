---
name: cn-ocr
slug: cn-ocr
displayName: 中文OCR
summary: 用 Tesseract 本地提取中文文本和词级坐标/置信度，支持竖排、繁体与目录批量处理。
license: MIT
description: 中文 OCR 和结构化文字提取。使用 Tesseract 输出纯文本或带词级坐标与置信度的 JSON，支持简体、繁体、竖排和目录批量处理，数据保留在本机。用于图片文字识别、扫描件转文字、截图 OCR、繁体/竖排识别和批量提取。
version: 0.1.2
homepage: https://github.com/axel286137079-dot/gaia-skill/tree/main/skills/cn-ocr
category: 工具效率
tags: [OCR, 文字识别, 图片转文字, 结构化, 中文识别]
platforms: [workbuddy, claude-code, cursor]
---

# 中文 OCR

把图片里的中文「看懂并转成结构化数据」——提取文本、按行/词输出坐标与置信度，支持竖排/繁体/批量，纯本地免 API key。

## 何时使用

- 用户要把图片/截图/扫描件/发票/证件里的文字提取出来
- 用户要批量识别一个文件夹的图片
- 用户要结构化输出（带坐标+置信度，方便后续对账/归档/表格还原）

## 依赖（自动探测）

| 工具 | 用途 | 缺失时 |
|---|---|---|
| tesseract（含中文语言包） | 中文 OCR 引擎 | `brew install tesseract tesseract-lang` |

## 工作流

### 单张识别
```bash
python3 bin/ocr.py 图片.png
python3 bin/ocr.py 图片.png --format json        # 结构化（坐标+置信度）
python3 bin/ocr.py 图片.png --lang chi_sim_vert  # 竖排文字
```

### 批量识别
```bash
python3 bin/ocr.py batch 图片目录/ --out ocr_out/
# 输出：每个图片一个 .txt + 汇总 _summary.json
```

## 参数速查

| 参数 | 说明 | 默认 |
|---|---|---|
| `--lang` | 语言：chi_sim 简横 / chi_sim_vert 简竖 / chi_tra 繁横 / chi_tra_vert 繁竖 | chi_sim |
| `--format` | text（纯文本）/ json（坐标+置信度） | text |
| `--clean` | 清洗中文间多余空格、全角归一化 | 关 |
| `--out` | 批量输出目录 | ocr_out |

## 边界与红线

- tesseract 对**手写、低清、印章、复杂表格**识别率有限，关键场景（发票/合同/证件）建议人工复核。
- 涉及个人隐私/敏感证件时，本地 OCR 数据不出本机是优势，但结果仍属敏感信息，注意保管。

## 参考

- Tesseract: https://github.com/tesseract-ocr/tesseract
