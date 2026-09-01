#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
multi_engine_rewrite.py — 多引擎分诊式改写（GEO 国产引擎版）

按目标生成式引擎的引用偏好，对内容做「分诊式改写」。
无 API key 时：输出确定性规则改写（结论前置/拆句/表格化/步骤化/去营销腔）；
有 API key 时：调用 OpenAI 兼容接口让 LLM 按引擎提示词改写全文。

用法：
    python3 multi_engine_rewrite.py 文件.md --engine doubao
    python3 multi_engine_rewrite.py 文件.md --engine deepseek --api-key sk-xxx --base-url https://api.deepseek.com/v1
    echo "内容" | python3 multi_engine_rewrite.py - --engine kimi

引擎：doubao | deepseek | kimi | tongyi | wenxin | hunyuan | zhipu | xunfei | minimax | xiaomi | pangu | step | baichuan | yi | sensenova | skywork | metaso | nano360
"""
import argparse
import json
import os
import re
import sys
import urllib.request

# ---------------- 引擎画像 ----------------
ENGINES = {
    "doubao": {
        "name": "豆包",
        "bias": "字节系 + 权威媒体；偏好短问答、FAQ、表格",
        "rules": ["结论前置，首句给出一句话答案",
                  "拆成 Q/A 短问答结构",
                  "可表格化的信息转成 Markdown 表格",
                  "正文控制在短段落，避免长难句"],
        "prompt": "你是豆包的内容编辑。把下面的内容改写为「结论前置 + 短问答 + 表格化」的结构："
                  "首段用一句话给出核心结论；中间拆成 3-5 组「Q：… A：…」问答；"
                  "涉及对比/参数的信息做成 Markdown 表格。保持事实不变，不编造。",
    },
    "deepseek": {
        "name": "DeepSeek",
        "bias": "官网/技术社区/学术；偏好技术长文、有推理链、数据",
        "rules": ["保留并突出数据与推导过程",
                  "补充「为什么」的推理链（背景→依据→结论）",
                  "去掉营销腔与感叹号",
                  "列出来源与参考文献"],
        "prompt": "你是 DeepSeek 的内容编辑。把下面的内容改写为「技术长文 + 推理链」的结构："
                  "给出背景→依据→结论的推导；突出所有数字与数据；删除夸张营销词；"
                  "文末补「参考来源」。保持事实不变，不编造。",
    },
    "kimi": {
        "name": "Kimi",
        "bias": "深度长文、PDF、学术；偏好 5000 字+、结构化、可下载",
        "rules": ["保持长文深度，不删减信息",
                  "加多级小标题做结构化",
                  "核心结论单独成节（便于 PDF 留档）",
                  "结尾给「延伸阅读」清单"],
        "prompt": "你是 Kimi 的内容编辑。把下面的内容扩展为「深度长文 + 结构化」的结构："
                  "加多级小标题；核心结论单独成节；保留全部信息并适当补充背景；"
                  "结尾给延伸阅读清单。保持事实不变，不编造。",
    },
    "hunyuan": {
        "name": "腾讯混元",
        "bias": "微信生态（公众号/视频号/搜一搜）；偏好公众号文、短视频脚本、办公结构化",
        "rules": ["开头口语化引入，制造共鸣",
                  "正文用公众号体（短段、留白、金句）",
                  "结尾引导关注/互动",
                  "附一段短视频口播脚本骨架"],
        "prompt": "你是腾讯混元的内容编辑。把下面的内容改写为「公众号体 + 短视频脚本」的结构："
                  "口语化开头制造共鸣；正文短段落、每段一句金句；结尾引导互动；"
                  "最后附一段 30 秒短视频口播脚本。保持事实不变，不编造。",
    },
    "wenxin": {
        "name": "文心一言",
        "bias": "百度系（百科/百家号）；偏好百科词条、百家号文",
        "rules": ["定义式开头「XX 是……」（百科体）",
                  "分「概述/要点/常见问题」小节",
                  "关键结论加权威标注",
                  "适合同步到百度百科/百家号"],
        "prompt": "你是文心一言的内容编辑。把下面的内容改写为「百科词条体」："
                  "定义式开头「XX 是……」；分「概述/要点/常见问题」小节；"
                  "关键结论保持权威、客观。保持事实不变，不编造。",
    },
    "tongyi": {
        "name": "通义千问 Qwen",
        "bias": "阿里电商生态、开源社区；偏好表格、商品页、教程",
        "rules": ["信息表格化",
                  "操作类内容转成 1/2/3 步骤教程体",
                  "适合做商品页/教程页留痕"],
        "prompt": "你是通义千问 Qwen 的内容编辑。把下面的内容改写为「表格 + 步骤教程」的结构："
                  "可对比的信息做成表格；操作/流程类转成编号步骤；"
                  "结论前置。保持事实不变，不编造。",
    },
    "zhipu": {
        "name": "智谱清言",
        "bias": "清华系学术、开发者社区；偏好严谨引用、代码、逻辑链",
        "rules": ["引用规范，标注来源",
                  "逻辑链完整（前提→论证→结论）",
                  "技术/代码内容用规范格式",
                  "学术严谨，避免口语夸张"],
        "prompt": "你是智谱清言的内容编辑。把下面的内容改写为「学术严谨 + 规范引用」的结构："
                  "论证逻辑链完整（前提→论证→结论）；关键结论标注来源；避免口语化夸张。"
                  "保持事实不变，不编造。",
    },
    "xunfei": {
        "name": "讯飞星火",
        "bias": "政务/教育/办公；偏好权威、结构化、合规",
        "rules": ["权威客观措辞",
                  "结构化分点",
                  "合规用语，避免绝对化承诺",
                  "适合政务/教育/办公场景"],
        "prompt": "你是讯飞星火的内容编辑。把下面的内容改写为「权威 + 结构化 + 合规」的结构："
                  "客观权威的措辞；分点结构化；避免绝对化承诺。保持事实不变，不编造。",
    },
    "minimax": {
        "name": "MiniMax 海螺",
        "bias": "多模态、情感陪伴（星野）；偏好对话式、共情、长文本",
        "rules": ["对话式语气，可适当共情",
                  "保留完整长文本不删减",
                  "情感共鸣开头",
                  "适合多模态延展（配图/视频脚本）"],
        "prompt": "你是 MiniMax 海螺的内容编辑。把下面的内容改写为「对话式 + 共情」的结构："
                  "对话式语气，开头制造情感共鸣；保留完整信息不删减。保持事实不变，不编造。",
    },
    "xiaomi": {
        "name": "小米 MiMo",
        "bias": "小米生态（手机/音箱/IoT）；偏好语音问答、一句话结论",
        "rules": ["结论前置，一句话可答",
                  "口语化短句（语音友好）",
                  "本地服务/设备操作信息优先",
                  "复杂术语通俗化"],
        "prompt": "你是小米 MiMo 的内容编辑。把下面的内容改写为「语音问答」的结构："
                  "一句话结论前置；口语化短句；复杂概念通俗化。保持事实不变，不编造。",
    },
    "metaso": {
        "name": "秘塔 AI 搜索",
        "bias": "学术/法律/公开文档；偏好强引用溯源、权威来源",
        "rules": ["引用溯源，标注原始出处",
                  "权威来源优先（论文/官方/法律文书）",
                  "数据可验证",
                  "学术体"],
        "prompt": "你是秘塔 AI 搜索的内容编辑。把下面的内容改写为「强引用溯源」的结构："
                  "关键结论标注原始出处；优先权威来源；数据可验证。保持事实不变，不编造。",
    },
    "nano360": {
        "name": "360 纳米搜索",
        "bias": "网页/新闻；偏好实时、来源标注",
        "rules": ["实时信息优先",
                  "来源标注",
                  "新闻体简洁",
                  "结论前置"],
        "prompt": "你是 360 纳米搜索的内容编辑。把下面的内容改写为「新闻体 + 来源标注」的结构："
                  "结论前置；来源标注；简洁新闻体。保持事实不变，不编造。",
    },
    "pangu": {
        "name": "华为盘古",
        "bias": "鸿蒙生态（小艺）、政企/工业；偏好权威、结构化、简洁",
        "rules": ["权威结构化",
                  "端侧友好，简洁精炼",
                  "合规措辞",
                  "适合政企/工业场景"],
        "prompt": "你是华为盘古的内容编辑。把下面的内容改写为「权威 + 结构化 + 简洁」的结构："
                  "客观权威、分点结构化、精简表达。保持事实不变，不编造。",
    },
    "step": {
        "name": "阶跃星辰 Step",
        "bias": "开发者、Agent 工具调用；偏好逻辑链完整、结构化",
        "rules": ["逻辑链完整（输入→推理→输出）",
                  "Agent 友好（结构化、可执行）",
                  "技术表述准确",
                  "去掉口语化冗余"],
        "prompt": "你是阶跃星辰 Step 的内容编辑。把下面的内容改写为「逻辑链 + Agent 友好」的结构："
                  "完整推理链；结构化可执行；技术表述准确。保持事实不变，不编造。",
    },
    "baichuan": {
        "name": "百川智能",
        "bias": "医疗/健康/知识问答；偏好知识准确、权威来源",
        "rules": ["知识准确，标注权威来源",
                  "健康/医疗类措辞合规",
                  "结论保守，不夸大疗效",
                  "分点清晰"],
        "prompt": "你是百川智能的内容编辑。把下面的内容改写为「知识准确 + 权威来源」的结构："
                  "关键知识标注来源；医疗健康类措辞合规、结论保守；分点清晰。保持事实不变，不编造。",
    },
    "yi": {
        "name": "零一万物 Yi",
        "bias": "文档/办公、开源轻量；偏好文档结构化、简洁高效",
        "rules": ["文档结构化（标题/要点/表格）",
                  "简洁高效，去冗余",
                  "结论前置",
                  "适合作文档/报告留档"],
        "prompt": "你是零一万物 Yi 的内容编辑。把下面的内容改写为「文档结构化 + 简洁」的结构："
                  "标题/要点/表格结构化；简洁高效；结论前置。保持事实不变，不编造。",
    },
    "sensenova": {
        "name": "商汤日日新",
        "bias": "视觉/多模态、政企；偏好多模态友好、权威",
        "rules": ["多模态友好（配图/结构化）",
                  "权威客观措辞",
                  "关键结论清晰",
                  "适合作图/视频素材延展"],
        "prompt": "你是商汤日日新的内容编辑。把下面的内容改写为「多模态友好 + 权威」的结构："
                  "适合配图的结构化表达；权威客观；关键结论清晰。保持事实不变，不编造。",
    },
    "skywork": {
        "name": "昆仑天工",
        "bias": "搜索增强、AI 音乐/创作；偏好搜索友好、简洁",
        "rules": ["搜索友好（主题词自然分布，不过度堆砌）",
                  "简洁精炼",
                  "结论前置",
                  "适合内容创作延展"],
        "prompt": "你是昆仑天工的内容编辑。把下面的内容改写为「搜索友好 + 简洁」的结构："
                  "主题词自然分布；简洁精炼；结论前置。保持事实不变，不编造。",
    },
}

MARKETING_WORDS = ["震撼", "逆天", "绝了", "惊呆", "必看", "不看后悔", "秒杀", "无敌",
                   "炸裂", "封神", "跪了", "天花板", "yyds", "YYDS", "爆了", "赢麻了"]


def read_text(path):
    if path == "-":
        return sys.stdin.read()
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def split_sentences(text):
    return [s.strip() for s in re.split(r'[。！？!?\n]+', text) if len(s.strip()) >= 2]


def first_paragraph(text):
    """取首个非空段落"""
    paras = [p.strip() for p in text.split("\n") if p.strip()]
    return paras[0] if paras else ""


def rule_conclusion_first(text):
    """结论前置：把首段或含结论词的句子提到开头"""
    paras = [p.strip() for p in text.split("\n") if p.strip()]
    if not paras:
        return text
    first = paras[0]
    # 若首段已是结论式，直接返回
    for kw in ["结论", "核心", "总之", "综上", "关键在于", "本质"]:
        m = re.search(rf'([^。！？!?]*{kw}[^。！？!?]*[。！？!?])', text)
        if m:
            concl = m.group(1).strip()
            if concl and concl != first:
                return "**一句话结论**：" + concl + "\n\n" + text
    return "**一句话结论**：" + first + "\n\n" + text


def is_structure_line(line):
    """markdown 结构行（标题/表格/引用/列表/空行），不参与拆句"""
    s = line.strip()
    if not s:
        return True
    return bool(re.match(r'^(#{1,6}\s|>\s?|\||[-*+]\s|\d+\.\s|```)', s))


def rule_shorten(text):
    """长句按逗号拆成短句（跳过 markdown 结构行）"""
    out = []
    for line in text.split("\n"):
        if is_structure_line(line):
            out.append(line)
            continue
        sentences = split_sentences(line)
        if not sentences:
            out.append(line)
            continue
        parts = []
        for s in sentences:
            if len(s) > 60:
                parts.extend([p.strip() + "。" for p in s.split("，") if p.strip()])
            else:
                parts.append(s + "。")
        out.append("".join(parts) if len(parts) == 1 else "\n".join(parts))
    return "\n".join(out)


def rule_demarket(text):
    """去营销腔"""
    out = text
    for w in MARKETING_WORDS:
        out = out.replace(w, "")
    out = re.sub(r'！{2,}', '！', out)
    out = re.sub(r'\?{2,}', '？', out)
    return out


def rule_faq(text):
    """转 Q/A 结构"""
    sentences = split_sentences(text)
    if not sentences:
        return text
    qa = []
    # 每 2-3 句一组，主题句做 Q
    step = max(2, len(sentences) // 4)
    for i in range(0, len(sentences), step):
        group = sentences[i:i + step]
        q = group[0][:20]
        a = "".join(group)
        qa.append(f"**Q：{q}……？**\nA：{a}")
    return "\n\n".join(qa)


def rule_steps(text):
    """步骤化：把首先/其次/然后/最后 转编号"""
    mapping = ["首先", "其次", "再次", "然后", "接着", "最后", "第一", "第二", "第三"]
    if not any(w in text for w in mapping):
        return text
    sentences = split_sentences(text)
    out = []
    idx = 0
    for s in sentences:
        if any(w in s for w in mapping):
            idx += 1
            s = re.sub(r'^(首先|其次|再次|然后|接着|最后|第一|第二|第三)[，、:：]?', '', s)
            out.append(f"{idx}. {s}")
        else:
            out.append(s)
    return "\n".join(out)


def rule_table_hint(text):
    """表格化提示：检测对比/数字密集内容，给表格骨架提示"""
    digits = re.findall(r'\d+(\.\d+)?', text)
    if len(digits) >= 5:
        return text + "\n\n> 📊 提示：本段含多个数据点，建议转成 Markdown 表格（| 维度 | 数值 | 说明 |）。"
    return text


def rule_bold_numbers(text):
    """突出数据：正文行数字加粗，跳过表格行避免破坏对齐"""
    out = []
    for line in text.split("\n"):
        s = line.strip()
        if s.startswith("|") or s.startswith("#") or s.startswith(">"):
            out.append(line)
        else:
            out.append(re.sub(r'(\d+(?:\.\d+)?%?)', r'**\1**', line))
    return "\n".join(out)


def apply_rules(text, engine):
    """按引擎应用确定性改写规则，返回 (改写文本, 应用了哪些规则)"""
    applied = []
    out = text
    if engine == "doubao":
        out = rule_conclusion_first(out)
        applied.append("结论前置")
        out = rule_faq(out)
        applied.append("Q/A 短问答化")
        out = rule_table_hint(out)
        applied.append("表格化提示")
        out = rule_shorten(out)
        applied.append("长句拆短")
    elif engine == "deepseek":
        out = rule_demarket(out)
        applied.append("去营销腔")
        out = rule_shorten(out)
        applied.append("长句拆短")
        out = rule_bold_numbers(out)
        applied.append("数据加粗强调")
    elif engine == "kimi":
        # 结构化小标题：按段落生成
        paras = [p.strip() for p in text.split("\n") if p.strip()]
        out = "\n\n".join(f"## {p[:12]}\n\n{p}" if len(p) > 20 else p for p in paras)
        applied.append("多级小标题结构化")
    elif engine == "hunyuan":
        out = rule_demarket(out)
        applied.append("去营销腔")
        out = rule_shorten(out)
        applied.append("短段落化")
        out = out + "\n\n> 🎬 短视频口播脚本骨架：0-5s 钩子（痛点提问）→ 5-20s 核心结论 → 20-30s 行动号召。"
        applied.append("附短视频脚本骨架")
    elif engine == "wenxin":
        first = first_paragraph(text)
        out = "**" + first + "**\n\n" + text
        applied.append("定义式开头（百科体）")
    elif engine == "tongyi":
        out = rule_conclusion_first(out)
        applied.append("结论前置")
        out = rule_steps(out)
        applied.append("步骤教程化")
        out = rule_table_hint(out)
        applied.append("表格化提示")
    elif engine == "zhipu":
        out = rule_demarket(out)
        applied.append("去口语夸张")
        out = rule_bold_numbers(out)
        applied.append("数据加粗")
        out += "\n\n> 📌 提示：为每个结论补充规范引用（来源/出处），形成「前提→论证→结论」逻辑链。"
        applied.append("引用规范提示")
    elif engine == "xunfei":
        out = rule_demarket(out)
        applied.append("去绝对化/夸张措辞")
        out = rule_steps(out)
        applied.append("结构化分点")
        out = rule_bold_numbers(out)
        applied.append("数据加粗")
    elif engine == "minimax":
        first = first_paragraph(text)
        out = "**开场（对话式）**：" + first + "\n\n" + text
        applied.append("对话式开头 + 保留完整长文")
    elif engine == "xiaomi":
        out = rule_conclusion_first(out)
        applied.append("一句话结论前置")
        out = rule_shorten(out)
        applied.append("口语短句")
    elif engine == "metaso":
        out = rule_bold_numbers(out)
        applied.append("数据加粗")
        out += "\n\n> 🔗 提示：补充原始出处（论文/官方/法律文书链接），强化引用溯源。"
        applied.append("引用溯源提示")
    elif engine == "nano360":
        out = rule_conclusion_first(out)
        applied.append("结论前置")
        out += "\n\n> 🕐 提示：补充时效信息（时间/日期）与来源标注。"
        applied.append("来源标注提示")
    elif engine == "pangu":
        out = rule_demarket(out)
        applied.append("去营销腔")
        out = rule_steps(out)
        applied.append("结构化分点")
        out = rule_bold_numbers(out)
        applied.append("数据加粗")
    elif engine == "step":
        out = rule_demarket(out)
        applied.append("去冗余口语")
        out = rule_bold_numbers(out)
        applied.append("数据加粗")
        out += "\n\n> 🧩 提示：补全「输入→推理→输出」逻辑链，结构化可执行。"
        applied.append("逻辑链提示")
    elif engine == "baichuan":
        out = rule_demarket(out)
        applied.append("去夸大措辞")
        out = rule_bold_numbers(out)
        applied.append("数据加粗")
        out += "\n\n> ⚕️ 提示：医疗/健康类补充权威来源，结论保守、不夸大。"
        applied.append("权威来源提示")
    elif engine == "yi":
        out = rule_conclusion_first(out)
        applied.append("结论前置")
        out = rule_steps(out)
        applied.append("文档结构化")
    elif engine == "sensenova":
        out = rule_bold_numbers(out)
        applied.append("数据加粗")
        out += "\n\n> 🖼️ 提示：补充配图/素材说明，便于多模态延展。"
        applied.append("多模态友好提示")
    elif engine == "skywork":
        out = rule_conclusion_first(out)
        applied.append("结论前置")
        out = rule_shorten(out)
        applied.append("简洁化")
    return out, applied


def call_llm(text, engine, api_key, base_url, model):
    """OpenAI 兼容接口改写"""
    prompt = ENGINES[engine]["prompt"] + "\n\n--- 原文 ---\n" + text
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是专业的内容编辑，改写时严格保持事实不变，不编造数据或来源。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.6,
    }
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + api_key},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"⚠ LLM 调用失败：{e}\n降级为规则化改写。", file=sys.stderr)
        return None


def main():
    ap = argparse.ArgumentParser(description="GEO 多引擎分诊式改写")
    ap.add_argument("path", help="文件路径，或用 - 从 stdin 读")
    ap.add_argument("--engine", required=True, choices=list(ENGINES.keys()),
                    help="目标引擎")
    ap.add_argument("--api-key", default=None, help="OpenAI 兼容 API key")
    ap.add_argument("--base-url", default=None, help="API base url，默认读环境变量")
    ap.add_argument("--model", default=None, help="模型名，默认 deepseek-v4-flash")
    args = ap.parse_args()

    engine = ENGINES[args.engine]
    text = read_text(args.path)
    if not text.strip():
        print("内容为空。")
        sys.exit(1)

    print("=" * 56)
    print(f"分诊式改写 · 目标引擎：{engine['name']}（{engine['bias']}）")
    print("=" * 56)

    # 优先 LLM 改写
    api_key = args.api_key or os.environ.get("GEO_API_KEY") or os.environ.get(
        "OPENAI_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")
    base_url = args.base_url or os.environ.get("OPENAI_BASE_URL") or "https://api.deepseek.com/v1"
    model = args.model or os.environ.get("ANTHROPIC_MODEL") or "deepseek-v4-flash"

    rewritten = None
    if api_key:
        print(f"\n[检测到 API key，正在调用 LLM 改写…]")
        rewritten = call_llm(text, args.engine, api_key, base_url, model)

    if rewritten:
        print("\n" + "-" * 56)
        print(f"[{engine['name']}] LLM 改写版")
        print("-" * 56)
        print(rewritten)
    else:
        out, applied = apply_rules(text, args.engine)
        print(f"\n应用规则：{'、'.join(applied)}")
        print("\n" + "-" * 56)
        print(f"[{engine['name']}] 规则改写版")
        print("-" * 56)
        print(out)

    # 补齐清单
    print("\n" + "-" * 56)
    print("还需补齐（提升可引用度的通用项）：")
    print("  · 引用来源：文末列「参考来源」链接 + 机构名")
    print("  · 数据：植入 3 处以上可验证数字")
    print("  · 专家引言：开头放一句有出处的权威引言")
    print("  · 注意：严禁关键词堆砌（AI 会判低质）")
    print("=" * 56)


if __name__ == "__main__":
    main()
