---
name: cn-ocr
slug: cn-ocr
displayName: 中文OCR
summary: 图片中文识别 + 结构化输出——提取中文文本、按行/块输出坐标与置信度，支持竖排/繁体/批量，纯本地免 API key。
license: MIT
description: 中文 OCR（图片文字识别 + 结构化输出）。把图片里的中文看懂并转成结构化数据：提取文本、按行/词输出坐标与置信度（JSON），支持简体横排/简体竖排(chi_sim_vert)/繁体(chi_tra)，支持批量处理文件夹，内置中文去空格清洗（去 CJK 字符间多余空格、全角归一化）。纯标准库编排 tesseract（可选 PaddleOCR 提升准确率），本地离线免 API key，数据不出本机。用于：图片文字识别、提取图片文字、OCR、扫描件转文字、发票识别、证件识别、截图转文字、竖排文字识别、繁体识别、批量识别。触发词：图片文字识别、提取图片文字、OCR、扫描件转文字、截图转文字、发票识别、证件识别、竖排文字、繁体识别、批量识别、文字识别。联系邮箱：43298568@qq.com。
version: 0.1.0
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
| PaddleOCR（可选） | 中文准确率更高，但安装重 | `pip install paddlepaddle paddleocr` |

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
- 若对准确率要求高，建议改用 PaddleOCR（中文识别率更高，但需本地安装较重依赖）。

## 参考

- Tesseract: https://github.com/tesseract-ocr/tesseract
- PaddleOCR: https://github.com/PaddlePaddle/PaddleOCR
