# 中文 OCR

把图片里的中文**看懂并转成结构化数据**——提取文本、按行/词输出坐标与置信度，支持竖排/繁体/批量，纯本地免 API key。

## 一句话定位

通用 OCR 有，但「能直接给 AI 用的结构化输出 + 中文复杂版面（竖排/繁体）」是空白；本 skill 用 tesseract 中文语言包（含竖排/繁体）封装出统一 CLI，一条命令把图片变成结构化文本/JSON。

## 为什么值得做

- 财务/法务/行政每天要处理发票、合同、扫描件，中文 OCR 是高频刚需。
- 中文坑多：竖排、繁体、多栏表格、印章干扰——通用英文 OCR 处理不了，需要中文语言包 + 竖排专用模型。
- 本地 OCR 数据不出本机，天然适配隐私敏感场景。

## 快速开始

```bash
# 单张识别（文本）
python3 bin/ocr.py 图片.png

# 结构化输出（坐标 + 置信度）
python3 bin/ocr.py 图片.png --format json

# 竖排 / 繁体
python3 bin/ocr.py 图片.png --lang chi_sim_vert
python3 bin/ocr.py 图片.png --lang chi_tra

# 批量识别文件夹
python3 bin/ocr.py batch 图片目录/ --out ocr_out/
```

## 目录结构

```
cn-ocr/
├── SKILL.md      # 路由逻辑 + 参数 + 边界红线
├── README.md     # 本文件
└── bin/ocr.py    # 核心脚本（纯标准库编排，本地离线）
```

## 依赖

| 工具 | 用途 | 缺失时 |
|---|---|---|
| tesseract + tesseract-lang | 中文 OCR 引擎 | `brew install tesseract tesseract-lang` |
| PaddleOCR（可选） | 准确率更高 | `pip install paddlepaddle paddleocr` |

## 边界与红线

- tesseract 对手写/低清/印章/复杂表格识别率有限，关键场景需人工复核。
- 敏感证件结果注意保管，勿外泄。
- 高准确率场景建议 PaddleOCR。

## License

MIT
