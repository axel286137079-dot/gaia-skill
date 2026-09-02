# Gaia Skill Hardening Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Repair the published skills' functional and safety defects, make one repository authoritative, and produce validated SkillHub and Codex-compatible packages.

**Architecture:** Treat `_github_repo` as the canonical source. Keep SkillHub metadata in source `SKILL.md` files, generate strict Codex packages with a deterministic build script, and mirror the tested canonical skills back to the workspace copy only after verification. External GitHub and SkillHub mutations remain delegated to WorkBuddy.

**Tech Stack:** Python 3 standard library, `unittest`, shell-based CI, GitHub Actions.

---

### Task 1: Add regression tests for blocking defects

**Files:**
- Create: `tests/test_skill_scripts.py`

1. Add tests for TTS fallback, output extension safety, subtitle output naming, metadata validation, safe output paths, Home Assistant dry-run behavior, source independence, XHS overlap removal, and gold news health checks.
2. Run `python3 -m unittest discover -s tests -v` and confirm the new tests fail for the current code.

### Task 2: Repair media skills

**Files:**
- Modify: `skills/cn-tts/bin/tts.py`
- Modify: `skills/video-subtitle/bin/subtitle.py`
- Modify: `skills/cn-ocr/bin/ocr.py`
- Modify: corresponding `SKILL.md` files

1. Make edge-tts failures return cleanly and allow automatic fallback.
2. Prevent `say` from writing fake MP3 files and report batch failures accurately.
3. Make Whisper's generated filename deterministic, honor edge voice selection, and describe actual outputs.
4. Check OCR subprocess failures and implement the documented Unicode normalization.
5. Run focused unit tests.

### Task 3: Secure generators and device control

**Files:**
- Modify: `skills/skill-forge/bin/skill_forge.py`
- Modify: `skills/smart-home-mcp/bin/ha_cli.py`
- Modify: corresponding `SKILL.md` files

1. Validate JSON metadata and reject traversal names.
2. Quote YAML scalars and support explicit `skillhub` and `codex` profiles.
3. Make Home Assistant mutations dry-run by default and require stronger confirmation for dangerous domains.
4. Run focused unit tests.

### Task 4: Improve rule-based auditors

**Files:**
- Modify: `skills/cn-shendu-research/bin/source_audit.py`
- Modify: `skills/contract-review/bin/contract_scan.py`
- Modify: `skills/cross-border-listing/bin/listing_check.py`
- Modify: `skills/geo-cn/bin/citability_audit.py`
- Modify: `skills/gold-premarket/bin/gold_premarket.py`
- Modify: `skills/xhs-neirong-gongchang/bin/xhs_sensitive_check.py`
- Modify: corresponding `SKILL.md` files

1. Count independent domains rather than URLs and inspect list/table claims.
2. Remove overlapping keyword hits and add missing contract penalty detection.
3. Validate bullet lengths and label marketplace rules as configurable heuristics.
4. Complete the GEO heuristic dimensions and remove advice that rewards unsupported certainty.
5. Fix health checks and use explicit Asia/Shanghai time.
6. Run examples and unit tests.

### Task 5: Canonical packaging and metadata

**Files:**
- Create: `scripts/build_codex_skills.py`
- Create: `scripts/validate_skills.py`
- Create: `agents/openai.yaml` under each skill
- Add: `skills/digital-human/`
- Modify: `README.md`

1. Add `digital-human` to the canonical repository after its claims are narrowed to the implemented guide/pipeline.
2. Generate strict Codex packages without README files or SkillHub-only frontmatter.
3. Add UI metadata for all twelve skills.
4. Validate descriptions, names, referenced files, and duplicate trees.

### Task 6: Continuous verification

**Files:**
- Create: `.github/workflows/validate.yml`

1. Run compile checks, unit tests, skill validation, Codex package builds, and example smoke tests.
2. Run the complete local verification suite.
3. Review `git diff --check` and repository status.

### Task 7: Mirror and external handoff

1. Mirror the verified canonical `skills/` tree to the workspace-level copy.
2. Ask WorkBuddy to inspect the diff, commit and push the approved changes, then update only changed SkillHub packages.
3. Ask WorkBuddy to return commit, remote, SkillHub version, and audit evidence for final verification.
