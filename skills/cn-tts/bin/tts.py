#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tts.py — 中文语音合成（文本转语音 TTS）

把中文文本转成自然语音，支持 14 种音色 + 语速/音调/音量调节 + 批量合成。
引擎自动选择：edge-tts（需联网）→ say（macOS 本地离线回退）。

纯标准库编排，无 API key。

用法：
    # 单条合成（指定音色）
    python3 tts.py "你好，这是合成示例。" --voice 晓晓 --out 音频.mp3

    # 从文件合成
    python3 tts.py --file 文本.txt --voice 云希 --rate +10%

    # 批量合成（JSON manifest）
    python3 tts.py batch manifest.json --out-dir out/

    # 查看全部中文音色
    python3 tts.py list-voices
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import hashlib
import stat
import uuid
from pathlib import Path

# ---------------- 中文音色表 ----------------
# 昵称 → (edge-tts 完整名, 说明)；昵称可作 --voice 的快捷写法
VOICES = {
    "晓晓": ("zh-CN-XiaoxiaoNeural", "女·温暖"),
    "晓伊": ("zh-CN-XiaoyiNeural", "女·活泼"),
    "云健": ("zh-CN-YunjianNeural", "男·激情"),
    "云希": ("zh-CN-YunxiNeural", "男·阳光"),
    "云夏": ("zh-CN-YunxiaNeural", "男·可爱"),
    "云扬": ("zh-CN-YunyangNeural", "男·专业新闻"),
    "晓北": ("zh-CN-liaoning-XiaobeiNeural", "女·东北口音"),
    "晓妮": ("zh-CN-shaanxi-XiaoniNeural", "女·陕西口音"),
    "曉佳": ("zh-HK-HiuGaaiNeural", "女·粤语"),
    "曉曼": ("zh-HK-HiuMaanNeural", "女·粤语"),
    "雲龍": ("zh-HK-WanLungNeural", "男·粤语"),
    "曉臻": ("zh-TW-HsiaoChenNeural", "女·台腔"),
    "曉雨": ("zh-TW-HsiaoYuNeural", "女·台腔"),
    "雲哲": ("zh-TW-YunJheNeural", "男·台腔"),
}

# say 回退音色（macOS）
SAY_VOICES = {"婷婷": "Tingting", "美佳": "Meijia", "善怡": "Sinji"}

DEFAULT_PATHS = {
    "edge-tts": ["/opt/homebrew/bin/edge-tts", "/usr/local/bin/edge-tts"],
    "say": ["/usr/bin/say"],
}

DEFAULT_TIMEOUT_SECONDS = 300
MIN_TIMEOUT_SECONDS = 1
MAX_TIMEOUT_SECONDS = 3600


def find_tool(name):
    p = shutil.which(name)
    if p:
        return p
    for cand in DEFAULT_PATHS.get(name, []):
        if os.path.exists(cand):
            return cand
    return None


# 2026-10-12 起微软轮换 Sec-MS-GEC 令牌至 v2，7.2 系列及更早版本全部 403。
EDGE_TTS_MIN_VERSION = (7, 3, 1)


def edge_tts_version(edge_path):
    """返回 (版本元组, 原始字符串)；无法解析时返回 (None, None)。"""
    try:
        proc = subprocess.run([edge_path, "--version"], check=False,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              timeout=15)
        raw = proc.stdout.decode("utf-8", "replace").strip()
    except (OSError, subprocess.SubprocessError):
        return None, None
    digits = re.search(r"(\d+)\.(\d+)\.(\d+)", raw)
    if not digits:
        return None, raw
    return tuple(int(x) for x in digits.groups()), raw


def check_edge_tts_version(edge_path):
    """低版本前置告警；返回 False 表示低于红线（仍继续尝试，但会明确提示）。"""
    ver, raw = edge_tts_version(edge_path)
    if ver is None:
        return True  # 探测失败不阻断，交由实际调用结果说话
    if ver < EDGE_TTS_MIN_VERSION:
        print("⚠ edge-tts %s 已失效：2026-10-12 起微软轮换 Sec-MS-GEC 令牌，"
              "7.2 系列及更早版本一律 403。" % ".".join(map(str, ver)), file=sys.stderr)
        print("  修复：pip install --upgrade 'edge-tts>=7.3.1'", file=sys.stderr)
        return False
    return True


def resolve_voice(name):
    """把昵称或完整名解析为 edge-tts 完整名；返回 None 表示不是 edge 音色。"""
    if name in VOICES:
        return VOICES[name][0]
    if name in [v[0] for v in VOICES.values()]:
        return name
    return None


def synth_edge_tts(text, voice, rate, pitch, volume, out,
                   timeout_seconds=DEFAULT_TIMEOUT_SECONDS):
    edge = find_tool("edge-tts")
    if not edge:
        print("✗ 未找到 edge-tts。安装：pip install 'edge-tts>=7.3.1'", file=sys.stderr)
        print("  或加 --engine say 用 macOS 本地 say 回退。", file=sys.stderr)
        return None
    check_edge_tts_version(edge)
    cmd = [edge, "--voice", voice, "--text", text,
           "--rate=" + rate, "--pitch=" + pitch, "--volume=" + volume,
           "--write-media", out]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        print("⚠ edge-tts 超时（超过 %s 秒）。" % timeout_seconds, file=sys.stderr)
        return None
    except (OSError, subprocess.CalledProcessError) as exc:
        msg = safe_error(exc)
        print("⚠ edge-tts 失败：" + msg, file=sys.stderr)
        if "403" in msg or "Invalid response status" in msg:
            print("  403 排障：① 升级 pip install --upgrade 'edge-tts>=7.3.1'"
                  "（7.2.x 已因令牌轮换失效）；② 检查本机时钟偏差，7.3.x 会自动读服务端"
                  " Date 头校准；③ 若 --list-voices 也 403 才考虑出口 IP 受限，可试 --proxy。",
                  file=sys.stderr)
        return None
    return out


def synth_say(text, voice, out, timeout_seconds=DEFAULT_TIMEOUT_SECONDS):
    say = find_tool("say")
    if not say:
        print("✗ 未找到 say（仅 macOS）。", file=sys.stderr)
        return None
    root, ext = os.path.splitext(out)
    if ext.lower() not in (".aif", ".aiff", ".wav"):
        safe_out = root + ".aiff"
        print("  ↳ say 不输出该格式，使用 AIFF。")
        out = safe_out
    v = SAY_VOICES.get(voice, voice)  # 允许直接传 Tingting 等
    cmd = [say, "-v", v, "-o", out]
    if os.path.splitext(out)[1].lower() == '.wav':
        cmd.extend(['--file-format=WAVE', '--data-format=LEI16'])
    try:
        subprocess.run(cmd, input=text.encode("utf-8"), check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        print("✗ say 超时（超过 %s 秒）。" % timeout_seconds, file=sys.stderr)
        return None
    except (OSError, subprocess.CalledProcessError) as exc:
        print("✗ say 失败：" + safe_error(exc), file=sys.stderr)
        return None
    return out


def safe_error(exc):
    """Do not stringify exceptions whose command or filename contains user data."""
    if isinstance(exc, subprocess.CalledProcessError):
        return "退出码 " + str(exc.returncode)
    return type(exc).__name__


def synth(text, voice, rate, pitch, volume, out, engine, timeout_seconds,
          attempt=None):
    """按引擎合成；engine='auto' 时优先 edge-tts，回退 say。"""
    def call(function, *args):
        kwargs = {} if timeout_seconds == DEFAULT_TIMEOUT_SECONDS else {
            'timeout_seconds': timeout_seconds
        }
        return attempt(function, *args, **kwargs) if attempt else function(*args, **kwargs)
    edge_voice = resolve_voice(voice)
    if engine == 'edge-tts' and edge_voice is None:
        edge_voice = voice  # Explicit engine validates its own non-preset names.
    if engine == "say":
        return call(synth_say, text, voice, out)
    if engine == "edge-tts" or (engine == "auto" and edge_voice):
        generated = call(synth_edge_tts, text, edge_voice, rate, pitch, volume, out)
        if generated:
            return generated
        if engine == "edge-tts":
            return None
        print("  ↳ edge-tts 失败，回退 say …")
    return call(synth_say, text, voice, out)


def say_target(out):
    root, ext = os.path.splitext(out)
    return out if ext.lower() in ('.aif', '.aiff', '.wav') else root + '.aiff'


def canonical_entry(value):
    path = Path(value)
    return path.parent.resolve() / path.name


def inode(s):
    return (s.st_dev, s.st_ino)


def stamp(s):
    return None if s is None else (inode(s), s.st_mode, s.st_size, s.st_mtime_ns)


def entry(fd, name):
    try:
        return os.stat(name, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        return None


def regular_or_absent(s):
    if s is not None and not stat.S_ISREG(s.st_mode):
        raise ValueError('目标不是普通文件')


def native_capabilities():
    required = (os.open, os.stat, os.mkdir, os.unlink, os.link, os.rename, os.rmdir)
    if not all(f in os.supports_dir_fd for f in required) or not hasattr(os, 'O_NOFOLLOW') or not hasattr(os, 'O_DIRECTORY'):
        raise ValueError('当前系统缺少安全目录句柄能力')


def protected_materials(extra):
    root = Path(__file__).resolve().parents[1]
    # Protect existing delivered materials, not arbitrary new audio under this cwd.
    names = ('SKILL.md', 'README.md', 'bin/tts.py', 'agents/openai.yaml',
             'examples/manifest.json', 'references/guide.md',
             'references/normal-input.json', 'references/normal-output.json',
             'references/failed-input.json', 'references/failed-output.json',
             'references/malicious-input.json', 'references/malicious-output.json')
    materials = [root / n for n in names] + [Path(p) for p in extra]
    return {canonical_entry(p) for p in materials} | {p.resolve() for p in materials}


def clean_owned(fd, name, expected):
    current = entry(fd, name)
    if current is None:
        return True
    if inode(current) != expected or not stat.S_ISREG(current.st_mode):
        return False
    os.unlink(name, dir_fd=fd)
    return True


def deliver(job, protected, committed):
    """Per-attempt staging and cooperative commit; NOT inode-CAS or engine sandbox."""
    native_capabilities()
    requested = canonical_entry(job['out'])
    variants = [requested]
    if job['engine'] in ('say', 'auto'):
        variants.append(canonical_entry(say_target(str(requested))))
    if requested in protected:
        raise ValueError('目标与输入或已安装材料冲突')
    parent = requested.parent
    fd = os.open(str(parent), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    parent_id = inode(os.fstat(fd))
    locks = []
    attempts = []
    committed_here = False
    errors = []
    result = None
    snapshots = {}

    def verify_parent():
        if inode(os.stat(str(parent))) != parent_id:
            raise ValueError('输出父目录已变化')

    def lock(name):
        token = '.cn-tts-' + hashlib.sha256(name.encode('utf-8')).hexdigest() + '.lock'
        if any(n == token for n, _ in locks):
            return
        f = os.open(token, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        try:
            locks.append((token, inode(os.fstat(f))))
        finally:
            os.close(f)

    def attempt(function, *args, **kwargs):
        verify_parent()
        name = '.cn-tts-attempt-' + uuid.uuid4().hex
        os.mkdir(name, 0o700, dir_fd=fd)
        af = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
        state = {'name': name, 'fd': af, 'inode': inode(os.fstat(af)), 'files': {}}
        attempts.append(state)
        temporary = str(parent / name / requested.name)
        actual_expected = say_target(temporary) if function is synth_say else temporary
        try:
            try:
                produced = function(*(args[:-1] + (temporary,)), **kwargs)
            except subprocess.TimeoutExpired:
                # Defense in depth for engine adapters that propagate the timeout.
                # Never stringify it: the command may contain the user's text.
                print('✗ 引擎调用超时。', file=sys.stderr)
                produced = None
        finally:
            # Only the expected engine output is ours; unknown names are never adopted.
            child = Path(actual_expected).name
            s = entry(af, child)
            if s is not None and stat.S_ISREG(s.st_mode):
                state['files'][child] = inode(s)
        verify_parent()
        if not produced or os.path.abspath(str(produced)) != actual_expected:
            return None
        leaf = Path(actual_expected).name
        s = entry(af, leaf)
        if s is None or not stat.S_ISREG(s.st_mode) or s.st_size <= 0:
            print('⚠ 本次引擎没有交付非空普通文件。', file=sys.stderr)
            return None
        read_fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=af)
        try:
            if stamp(os.fstat(read_fd)) != stamp(s):
                raise ValueError('本次产物已变化')
        finally:
            os.close(read_fd)
        state['selected'] = (leaf, stamp(s))
        return actual_expected

    try:
        verify_parent()
        snapshots.update({p.name: entry(fd, p.name) for p in variants})
        lock(requested.name)
        # Reject selected deterministic target before synthesis; auto alternatives remain conditional.
        deterministic = Path(say_target(str(requested))).name if job['engine'] == 'say' else requested.name
        regular_or_absent(snapshots[deterministic])
        generated = synth(job['text'], job['voice'], job['rate'], job['pitch'], job['volume'],
                          str(requested), job['engine'], job['timeout_seconds'], attempt=attempt)
        if not generated:
            raise ValueError('合成失败或本次产物无效')
        state = next(a for a in attempts if str(parent / a['name'] / a.get('selected', ('',))[0]) == generated)
        leaf, source_stamp = state['selected']
        actual = parent / leaf
        if actual in protected:
            raise ValueError('实际目标与输入或已安装材料冲突')
        verify_parent()
        lock(leaf)
        initial = snapshots[leaf]
        regular_or_absent(initial)
        now = entry(fd, leaf)
        if (parent_id, leaf) in committed or (now is not None and inode(now) in {v for v in committed.values()}):
            raise ValueError('本批实际目标重复')
        if stamp(now) != stamp(initial):
            raise ValueError('目标在本次处理期间变化')
        visible_stage = entry(fd, state['name'])
        if visible_stage is None or inode(visible_stage) != state['inode'] or stamp(entry(state['fd'], leaf)) != source_stamp:
            raise ValueError('本次暂存产物身份变化')
        if initial is None:
            os.link(leaf, leaf, src_dir_fd=state['fd'], dst_dir_fd=fd, follow_symlinks=False)
        else:
            os.replace(leaf, leaf, src_dir_fd=state['fd'], dst_dir_fd=fd)
        committed_here = True
        committed[(parent_id, leaf)] = inode(entry(fd, leaf))
        result = str(actual)
    except (OSError, ValueError, StopIteration) as exc:
        errors.append(safe_error(exc))
    finally:
        for a in attempts:
            try:
                visible = entry(fd, a['name'])
                if visible is None or inode(visible) != a['inode'] or not stat.S_ISDIR(visible.st_mode):
                    raise ValueError('暂存目录身份变化，未清理未知对象')
                for name, identity in a['files'].items():
                    if not clean_owned(a['fd'], name, identity):
                        raise ValueError('暂存文件身份变化，未清理未知对象')
                os.rmdir(a['name'], dir_fd=fd)
            except (OSError, ValueError) as exc:
                errors.append('清理:' + safe_error(exc))
            finally:
                os.close(a['fd'])
        for name, identity in reversed(locks):
            try:
                if not clean_owned(fd, name, identity):
                    raise ValueError('锁身份变化，未清理未知对象')
            except (OSError, ValueError) as exc:
                errors.append('清理:' + safe_error(exc))
        os.close(fd)
    if errors:
        phase = '已提交但后续失败' if committed_here else '未提交'
        raise ValueError(phase + '；' + '；'.join(errors))
    return result


def validate_timeout_seconds(value):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError('超时秒数必须是整数')
    if not MIN_TIMEOUT_SECONDS <= value <= MAX_TIMEOUT_SECONDS:
        raise ValueError('超时秒数必须在 1 到 3600 之间')
    return value


def timeout_argument(value):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError('必须是 1 到 3600 的整数')
    if str(parsed) != str(value).strip() or not MIN_TIMEOUT_SECONDS <= parsed <= MAX_TIMEOUT_SECONDS:
        raise argparse.ArgumentTypeError('必须是 1 到 3600 的整数')
    return parsed


def validate_job(item, out, engine, index,
                 timeout_seconds=DEFAULT_TIMEOUT_SECONDS):
    if not isinstance(item, dict):
        raise ValueError('第 %s 条必须是对象' % index)
    defaults = {'text': '', 'voice': '晓晓', 'rate': '+0%', 'pitch': '+0Hz', 'volume': '+0%'}
    job = {}
    for key, default in defaults.items():
        value = item.get(key, default)
        if not isinstance(value, str) or (key == 'text' and not value.strip()):
            raise ValueError('第 %s 条字段 %s 无效' % (index, key))
        job[key] = value.strip() if key == 'text' else value
    requested = item.get('out')
    if requested is not None and not isinstance(requested, str):
        raise ValueError('第 %s 条字段 out 无效' % index)
    job.update(out=out, engine=engine,
               timeout_seconds=validate_timeout_seconds(timeout_seconds))
    # Path construction is validation, not authorization to write.
    if not isinstance(out, str) or not Path(out).name or '\x00' in out:
        raise ValueError('第 %s 条输出路径无效' % index)
    return job


def preflight(jobs, protected):
    targets = set()
    for job in jobs:
        requested = canonical_entry(job['out'])
        actual = canonical_entry(say_target(str(requested))) if job['engine'] == 'say' else requested
        if requested in protected or actual in protected:
            raise ValueError('目标与输入或已安装材料冲突')
        if actual in targets:
            raise ValueError('批次目标重复')
        targets.add(actual)


def cmd_single(args):
    text = args.text
    if args.file:
        with open(args.file, "r", encoding="utf-8") as f:
            text = f.read()
    if not text or not text.strip():
        print("✗ 没有文本可合成。", file=sys.stderr)
        sys.exit(1)
    out = args.out or "out.mp3"
    print(f"  合成中（音色={args.voice}, 语速={args.rate}）→ {out}")
    job = validate_job(dict(text=text, voice=args.voice, rate=args.rate, pitch=args.pitch, volume=args.volume), out, args.engine, 1, args.timeout_seconds)
    protected = protected_materials([args.file] if args.file else [])
    preflight([job], protected)
    generated = deliver(job, protected, {})
    if generated:
        print(f"✓ 音频已生成：{generated}")
        return
    sys.exit(1)


def cmd_batch(args):
    with open(args.manifest, "r", encoding="utf-8") as f:
        items = json.load(f)
    if not isinstance(items, list):
        raise ValueError('批次根必须是数组')
    jobs = []
    for i, item in enumerate(items, 1):
        if not isinstance(item, dict):
            raise ValueError('第 %s 条必须是对象' % i)
        raw = item.get('out')
        if raw is not None and not isinstance(raw, str):
            raise ValueError('第 %s 条字段 out 无效' % i)
        out = raw or f'{i:04d}.mp3'
        if not os.path.isabs(out):
            out = os.path.join(args.out_dir, out)
        jobs.append(validate_job(item, out, args.engine, i, args.timeout_seconds))
    protected = protected_materials([args.manifest])
    preflight(jobs, protected)
    os.makedirs(args.out_dir, exist_ok=True)
    total = len(items)
    failures = []
    generated_files = []
    committed = {}
    for i, job in enumerate(jobs, 1):
        out = job['out']
        print(f"  [{i}/{total}] 合成 → {out}")
        try:
            generated = deliver(job, protected, committed)
            if not generated:
                raise RuntimeError("合成失败")
            generated_files.append(generated)
        except (OSError, ValueError) as e:
            print(f"  ✗ 第 {i} 条失败：{e}", file=sys.stderr)
            failures.append(i)
    print(f"\n批量结果：成功 {len(generated_files)} / {total} → {args.out_dir}")
    if failures:
        print(f"✗ 失败条目：{', '.join(map(str, failures))}", file=sys.stderr)
        sys.exit(1)


def cmd_list_voices(args):
    print("edge-tts 中文音色（14 种）：")
    print(f"{'昵称':<6}{'完整名':<36}{'风格'}")
    print("-" * 56)
    for nick, (full, desc) in VOICES.items():
        print(f"{nick:<6}{full:<36}{desc}")
    print("\nmacOS say 回退音色：")
    for nick, full in SAY_VOICES.items():
        print(f"  {nick} ({full})")


def _parse_synth_args(argv):
    """解析单条合成参数（text 可为位置参数，或用 --file）"""
    ap = argparse.ArgumentParser(description="单条合成：文本 → 语音", add_help=False)
    ap.add_argument("text", nargs="?", help="要合成的文本")
    ap.add_argument("--file", help="从文本文件读取")
    ap.add_argument("--voice", default="晓晓", help="音色（昵称或完整名）")
    ap.add_argument("--rate", default="+0%", help="语速，如 +10%%")
    ap.add_argument("--pitch", default="+0Hz", help="音调，如 +10Hz")
    ap.add_argument("--volume", default="+0%", help="音量，如 +20%%")
    ap.add_argument("--engine", default="auto", choices=["auto", "edge-tts", "say"])
    ap.add_argument("--out", default=None, help="输出音频路径")
    ap.add_argument("--timeout-seconds", type=timeout_argument,
                    default=DEFAULT_TIMEOUT_SECONDS,
                    help="单次引擎调用超时秒数（1-3600，默认 300）")
    return ap.parse_args(argv)


def _parse_batch_args(argv):
    ap = argparse.ArgumentParser(description="批量合成", add_help=False)
    ap.add_argument("manifest", help="manifest.json：[{text,voice,rate,pitch,out}...]")
    ap.add_argument("--out-dir", default="tts_out", help="输出目录")
    ap.add_argument("--engine", default="auto", choices=["auto", "edge-tts", "say"])
    ap.add_argument("--timeout-seconds", type=timeout_argument,
                    default=DEFAULT_TIMEOUT_SECONDS,
                    help="每条单次引擎调用超时秒数（1-3600，默认 300）")
    return ap.parse_args(argv)


def print_usage():
    print("""中文语音合成 tts.py

用法：
  python3 tts.py "要合成的文本" [--voice 晓晓] [--rate +10%] [--out 音频.mp3]
  python3 tts.py --file 文本.txt [--voice 云希] [--engine say]
  python3 tts.py batch manifest.json [--out-dir out/]
  python3 tts.py list-voices

参数：--voice 音色 / --rate 语速 / --pitch 音调 / --volume 音量 / --engine 引擎 / --out 输出 / --timeout-seconds 超时秒数""")


def main():
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        print_usage()
        return
    cmd = argv[0]
    if cmd == "list-voices":
        cmd_list_voices(None)
        return
    if cmd == "batch":
        args = _parse_batch_args(argv[1:])
        cmd_batch(args)
        return
    if cmd == "synth":
        argv = argv[1:]
    args = _parse_synth_args(argv)
    cmd_single(args)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, UnicodeError) as exc:
        # ValueError messages are generated locally and do not include field values.
        message = str(exc) if type(exc) is ValueError else safe_error(exc)
        print('✗ 处理失败：' + message, file=sys.stderr)
        sys.exit(1)
