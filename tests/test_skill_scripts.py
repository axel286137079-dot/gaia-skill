import argparse
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load_module(name, relative_path):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TtsTests(unittest.TestCase):
    def setUp(self):
        self.tts = load_module("tts_test", "skills/cn-tts/bin/tts.py")

    def test_edge_failure_is_reported_without_exception(self):
        with mock.patch.object(self.tts, "find_tool", return_value="/fake/edge-tts"), \
             mock.patch.object(self.tts.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "edge")):
            self.assertIsNone(self.tts.synth_edge_tts("text", "voice", "+0%", "+0Hz", "+0%", "out.mp3"))

    def test_auto_falls_back_to_say(self):
        with mock.patch.object(self.tts, "synth_edge_tts", return_value=None), \
             mock.patch.object(self.tts, "synth_say", return_value="out.aiff") as fallback:
            result = self.tts.synth("text", "晓晓", "+0%", "+0Hz", "+0%", "out.mp3", "auto")
        self.assertEqual(result, "out.aiff")
        fallback.assert_called_once()

    def test_say_changes_unsupported_extension(self):
        with mock.patch.object(self.tts, "find_tool", return_value="/usr/bin/say"), \
             mock.patch.object(self.tts.subprocess, "run") as run:
            result = self.tts.synth_say("text", "Tingting", "voice.mp3")
        self.assertEqual(result, "voice.aiff")
        self.assertEqual(run.call_args.args[0][-1], "voice.aiff")


class SubtitleTests(unittest.TestCase):
    def test_transcribe_renames_temp_audio_output(self):
        module = load_module("subtitle_test", "skills/video-subtitle/bin/subtitle.py")
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            audio = tmp_path / "audio.wav"
            audio.write_bytes(b"wav")
            out = tmp_path / "out"

            def fake_run(*args, **kwargs):
                out.mkdir(exist_ok=True)
                (out / "audio.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\n你好\n", encoding="utf-8")

            with mock.patch.object(module, "require_tool", return_value="whisper"), \
                 mock.patch.object(module.subprocess, "run", side_effect=fake_run):
                result = module.transcribe(str(audio), str(out), "tiny", "zh", "video")
            self.assertEqual(Path(result).name, "video.srt")
            self.assertTrue((out / "video.srt").exists())
            self.assertFalse((out / "audio.srt").exists())

    def test_edge_voice_defaults_to_mp3(self):
        module = load_module("subtitle_voice_test", "skills/video-subtitle/bin/subtitle.py")
        args = argparse.Namespace(srt="demo.srt", out=None, voice="zh-CN-XiaoxiaoNeural", tts="edge-tts")
        with mock.patch.object(module, "srt_to_voice", return_value="demo.mp3") as synth:
            module.cmd_voice(args)
        self.assertEqual(synth.call_args.args[1], "demo.mp3")


class GeneratorAndSafetyTests(unittest.TestCase):
    def test_skill_forge_rejects_path_traversal(self):
        module = load_module("forge_test", "skills/skill-forge/bin/skill_forge.py")
        meta = {"name": "../../escape", "displayName": "x", "summary": "x"}
        with self.assertRaises(ValueError):
            module.finalize_meta(meta)

    def test_codex_frontmatter_only_contains_required_fields(self):
        module = load_module("forge_codex_test", "skills/skill-forge/bin/skill_forge.py")
        meta = module.finalize_meta({"name": "demo-skill", "displayName": "Demo", "summary": "Does work"})
        frontmatter = module.render_frontmatter(meta, "codex")
        self.assertIn('name: "demo-skill"', frontmatter)
        self.assertIn("description:", frontmatter)
        self.assertNotIn("displayName:", frontmatter)

    def test_home_assistant_call_is_dry_run_without_credentials(self):
        module = load_module("ha_test", "skills/smart-home-mcp/bin/ha_cli.py")
        args = argparse.Namespace(domain="light", service="turn_on", entity="light.demo",
                                  data=None, execute=False, confirm_dangerous=False)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            module.cmd_call(args)
        self.assertIn("未执行", output.getvalue())

    def test_home_assistant_rejects_path_like_domain(self):
        module = load_module("ha_path_test", "skills/smart-home-mcp/bin/ha_cli.py")
        args = argparse.Namespace(domain="../api", service="turn_on", entity="light.demo",
                                  data=None, execute=False, confirm_dangerous=False)
        with self.assertRaises(SystemExit) as raised:
            module.cmd_call(args)
        self.assertEqual(raised.exception.code, 2)


class AuditorTests(unittest.TestCase):
    def test_source_audit_collapses_same_site(self):
        module = load_module("source_test", "skills/cn-shendu-research/bin/source_audit.py")
        self.assertEqual(module.source_identity("news.stats.gov.cn"), "stats.gov.cn")
        self.assertEqual(module.source_identity("www.stats.gov.cn"), "stats.gov.cn")

    def test_source_audit_keeps_list_and_table_claims(self):
        module = load_module("source_lines_test", "skills/cn-shendu-research/bin/source_audit.py")
        sentences = module.split_sentences("- 2026年增长35%。\n| 指标 | 1200万 |")
        self.assertTrue(any("35%" in sentence for sentence in sentences))
        self.assertTrue(any("1200" in sentence for sentence in sentences))

    def test_contract_prefers_longer_overlap_and_finds_high_penalty(self):
        module = load_module("contract_test", "skills/contract-review/bin/contract_scan.py")
        hits = module.scan("甲方不承担任何责任。违约金为合同额的50%")
        keywords = [hit["keyword"] for hit in hits]
        self.assertIn("不承担任何责任", keywords)
        self.assertNotIn("免责", keywords)
        self.assertIn("违约金 50%", keywords)

    def test_listing_checks_bullet_length(self):
        module = load_module("listing_test", "skills/cross-border-listing/bin/listing_check.py")
        issues = module.check("Title: Normal Product\n- " + "a" * 21, bullet_max=20)
        self.assertTrue(any(issue["type"] == "五点" for issue in issues))

    def test_xhs_prefers_longer_overlapping_term(self):
        module = load_module("xhs_test", "skills/xhs-neirong-gongchang/bin/xhs_sensitive_check.py")
        hits = module.check("这是稳赚不赔的承诺")
        self.assertEqual([hit["word"] for hit in hits], ["稳赚不赔"])

    def test_gold_empty_news_fails_health_check(self):
        module = load_module("gold_test", "skills/gold-premarket/bin/gold_premarket.py")
        output = io.StringIO()
        with mock.patch.object(module, "fetch_quote", return_value={"XAU": {}}), \
             mock.patch.object(module, "fetch_kline", return_value=[{"close": 1}]), \
             mock.patch.object(module, "fetch_news", return_value=[]), \
             contextlib.redirect_stdout(output):
            module.main(["gold", "test"])
        payload = json.loads(output.getvalue())
        self.assertFalse(payload["news"])


class OcrTests(unittest.TestCase):
    def test_clean_cjk_normalizes_full_width(self):
        module = load_module("ocr_test", "skills/cn-ocr/bin/ocr.py")
        self.assertEqual(module.clean_cjk("Ａ 中 文"), "A 中文")


if __name__ == "__main__":
    unittest.main()
