#!/usr/bin/env python3
"""Scrape ansible-core devel branch for deprecation notices and identify gaps.

Clones (or updates) the ansible/ansible devel branch into a local cache,
extracts all deprecation patterns, compares them against the existing APME
rule inventory, and outputs a gap report for any deprecations that lack a
corresponding rule.

The gap report is written as JSON to stdout (suitable for piping into a
GitHub Actions step that creates an issue) and optionally as a human-readable
markdown file.

Usage:
    python scripts/scrape_ansible_deprecations.py
    python scripts/scrape_ansible_deprecations.py --min-version 2.21 --audience content
    python scripts/scrape_ansible_deprecations.py --output-json /tmp/gaps.json
    python scripts/scrape_ansible_deprecations.py --skip-clone --cache-dir /tmp/ansible
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import textwrap
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CACHE = REPO_ROOT / ".cache" / "ansible-core"
ANSIBLE_REPO = "https://github.com/ansible/ansible.git"
ANSIBLE_LIB = "lib/ansible"

# Rule inventory paths
OPA_BUNDLE = REPO_ROOT / "src" / "apme_engine" / "validators" / "opa" / "bundle"
NATIVE_RULES = REPO_ROOT / "src" / "apme_engine" / "validators" / "native" / "rules"
ANSIBLE_RULES = REPO_ROOT / "src" / "apme_engine" / "validators" / "ansible" / "rules"

# ── Regex patterns for deprecation extraction ────────────────────────

_DEPRECATED_CALL = re.compile(
    r"""display\.deprecated\(\s*
        (?P<quote>["']{1,3})(?P<message>.*?)(?P=quote)
        [^)]*?
        version\s*=\s*["'](?P<version>[\d.]+)["']
    """,
    re.VERBOSE | re.DOTALL,
)

_DEPRECATED_CALL_POS = re.compile(
    r"""display\.deprecated\(\s*
        (?P<quote>["']{1,3})(?P<message>.*?)(?P=quote)\s*,\s*
        ["'](?P<version>[\d.]+)["']
    """,
    re.VERBOSE | re.DOTALL,
)

_DEPRECATED_DATE_CALL = re.compile(
    r"""display\.deprecated\(\s*
        (?P<quote>["']{1,3})(?P<message>.*?)(?P=quote)
        [^)]*?
        date\s*=\s*datetime\.date\((?P<year>\d+),\s*(?P<month>\d+),\s*(?P<day>\d+)\)
    """,
    re.VERBOSE | re.DOTALL,
)

_DEPRECATED_COMMENT = re.compile(
    r"#\s*deprecated:\s*(?P<version>[\d.]+)\s*(?:[-—:]\s*(?P<note>.*))?",
    re.IGNORECASE,
)

_DEPRECATED_TAG = re.compile(
    r"_tags\.Deprecated\(\s*(?:version\s*=\s*)?[\"'](?P<version>[\d.]+)[\"']",
)

_COLLECTION_NAME = re.compile(
    r"collection_name\s*=\s*[\"'](?P<collection>[^\"']+)[\"']",
)

# ── Audience classification ──────────────────────────────────────────

_CONTENT_PATHS = [
    "parsing/mod_args",
    "parsing/dataloader",
    "playbook/",
    "vars/manager",
    "vars/hostvars",
    "plugins/lookup/",
    "plugins/callback/tree",
    "plugins/callback/oneline",
    "plugins/connection/paramiko",
    "plugins/inventory/",
    "executor/task_executor",
    "plugins/filter/core",
    "plugins/action/include_vars",
    "_internal/_yaml/",
    "_internal/_templating/",
]

_DEV_PATHS = [
    "template/__init__",
    "module_utils/",
    "errors/",
    "compat/",
    "plugins/cache/base",
    "plugins/shell/",
    "parsing/yaml/objects",
    "parsing/ajson",
    "parsing/utils/jsonify",
    "utils/listify",
]

_CONTENT_KW = [
    "playbook",
    "task",
    "play_hosts",
    "ansible_hostname",
    "ansible_facts",
    "when:",
    "when ",
    "conditional",
    "args:",
    "action:",
    "connection:",
    "strategy:",
    "callback",
    "inventory",
    "!!omap",
    "!!pairs",
    "vault",
    "include",
    "yum_repository",
    "follow_redirects",
    "first_found",
    "variable name",
    "host_var",
    "group_var",
    "ignore_files",
    "paramiko_ssh",
    "tree callback",
    "oneline callback",
]

_DEV_KW = [
    "templar",
    "ansiblemodule",
    "jsonify",
    "exit_json",
    "fail_json",
    "import ",
    "api ",
    "class ",
    "method ",
    "function ",
    "argument_spec",
    "_available_variables",
    "do_template",
    "set_temporary_context",
    "copy_with_new_env",
    "module_utils",
    "suppress_extended_error",
    "AnsibleFilterTypeError",
    "_AnsibleActionDone",
    "importlib_resources",
    "ShellModule",
    "checksum()",
    "wrap_for_exec",
    "_encode_script",
]


def _classify_audience(filepath: str, message: str) -> str:
    for p in _CONTENT_PATHS:
        if p in filepath:
            return "content"
    for p in _DEV_PATHS:
        if p in filepath:
            return "developer"
    msg_lower = message.lower()
    for kw in _CONTENT_KW:
        if kw in msg_lower:
            return "content"
    for kw in _DEV_KW:
        if kw in msg_lower:
            return "developer"
    return "unknown"


def _fingerprint(source_file: str, line_number: int, message: str) -> str:
    raw = f"{source_file}:{line_number}:{message[:80]}"
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


# ── Data classes ─────────────────────────────────────────────────────


@dataclass
class DeprecationEntry:
    """A single deprecation notice extracted from ansible-core source.

    Attributes:
        source_file: Path of the file relative to lib/ansible.
        line_number: 1-based line where the notice was found.
        mechanism: How it was detected (e.g. display.deprecated, comment, tag).
        removal_version: Target removal version or date-prefixed string.
        message: Normalized deprecation text.
        fingerprint: Short stable hash for deduplication.
        context_lines: Surrounding source lines for context.
        collection_name: Declaring collection (default ansible.builtin).
        audience: content, developer, or unknown.
    """

    source_file: str
    line_number: int
    mechanism: str
    removal_version: str
    message: str
    fingerprint: str = ""
    context_lines: list[str] = field(default_factory=list)
    collection_name: str = "ansible.builtin"
    audience: str = "unknown"


# ── Git helpers ──────────────────────────────────────────────────────


def _run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kwargs)  # type: ignore[call-overload,no-any-return]


def clone_or_update(cache_dir: Path, branch: str = "devel") -> Path:
    """Clone ansible/ansible or fetch latest; return path to the repo.

    Args:
        cache_dir: Local directory for the clone or existing repo.
        branch: Remote branch to check out (default devel).

    Returns:
        Absolute path to the repository root.
    """
    if cache_dir.exists() and (cache_dir / ".git").exists():
        print(f"Updating existing clone at {cache_dir}…", file=sys.stderr)
        _run(["git", "fetch", "origin", branch], cwd=cache_dir)
        _run(["git", "checkout", f"origin/{branch}"], cwd=cache_dir)
    else:
        print(f"Cloning ansible-core into {cache_dir}…", file=sys.stderr)
        cache_dir.parent.mkdir(parents=True, exist_ok=True)
        _run(
            [
                "git",
                "clone",
                "--depth",
                "1",
                "--branch",
                branch,
                "--single-branch",
                ANSIBLE_REPO,
                str(cache_dir),
            ]
        )
    return cache_dir


def get_commit(repo_dir: Path) -> str:
    """Return the current HEAD full commit hash.

    Args:
        repo_dir: Root of the git checkout.

    Returns:
        Full 40-character commit SHA.
    """
    return _run(["git", "rev-parse", "HEAD"], cwd=repo_dir).stdout.strip()


def get_ansible_version(repo_dir: Path) -> str:
    """Extract ansible-core version from lib/ansible/release.py.

    Args:
        repo_dir: Root of the ansible/ansible checkout.

    Returns:
        __version__ string from release.py, or ``unknown`` if missing.
    """
    release_py = repo_dir / ANSIBLE_LIB / "release.py"
    if release_py.exists():
        text = release_py.read_text(encoding="utf-8")
        m = re.search(r"__version__\s*=\s*[\"']([^\"']+)[\"']", text)
        if m:
            return m.group(1)
    return "unknown"


# ── File scanning ────────────────────────────────────────────────────


def _get_context(lines: list[str], line_idx: int, window: int = 3) -> list[str]:
    start = max(0, line_idx - window)
    end = min(len(lines), line_idx + window + 1)
    return lines[start:end]


def scan_file(filepath: Path, base_dir: Path) -> list[DeprecationEntry]:
    """Scan a single Python file for all deprecation patterns.

    Args:
        filepath: Absolute path to the Python file under base_dir.
        base_dir: ansible lib root used to compute relative source_file paths.

    Returns:
        Extracted deprecation entries; empty list on read errors.
    """
    entries: list[DeprecationEntry] = []
    try:
        text = filepath.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return entries

    lines = text.splitlines()
    rel_path = str(filepath.relative_to(base_dir))
    seen_spans: set[tuple[int, int]] = set()

    def _add(m_start: int, mechanism: str, version: str, msg: str, collection: str = "ansible.builtin") -> None:
        line_no = text[:m_start].count("\n") + 1
        span_key = (line_no, hash(msg[:60]))
        if span_key in seen_spans:
            return
        seen_spans.add(span_key)
        msg = re.sub(r"\s+", " ", msg.strip())
        audience = _classify_audience(rel_path, msg)
        fp = _fingerprint(rel_path, line_no, msg)
        entries.append(
            DeprecationEntry(
                source_file=rel_path,
                line_number=line_no,
                mechanism=mechanism,
                removal_version=version,
                message=msg,
                fingerprint=fp,
                context_lines=_get_context(lines, line_no - 1),
                collection_name=collection,
                audience=audience,
            )
        )

    for m in _DEPRECATED_CALL.finditer(text):
        collection = "ansible.builtin"
        cm = _COLLECTION_NAME.search(m.group(0))
        if cm:
            collection = cm.group("collection")
        _add(m.start(), "display.deprecated", m.group("version"), m.group("message"), collection)

    for m in _DEPRECATED_CALL_POS.finditer(text):
        _add(m.start(), "display.deprecated", m.group("version"), m.group("message"))

    for m in _DEPRECATED_DATE_CALL.finditer(text):
        year, month, day = m.group("year"), m.group("month"), m.group("day")
        version = f"date:{year}-{month.zfill(2)}-{day.zfill(2)}"
        _add(m.start(), "display.deprecated", version, m.group("message"))

    for i, line in enumerate(lines):
        cm = _DEPRECATED_COMMENT.search(line)
        if cm:
            note = (cm.group("note") or "").strip()
            next_code = ""
            for j in range(i + 1, min(i + 5, len(lines))):
                stripped = lines[j].strip()
                if stripped and not stripped.startswith("#"):
                    next_code = stripped
                    break
            context_msg = note or next_code or f"Staged deprecation for removal in {cm.group('version')}"
            span_key = (i + 1, hash(context_msg[:60]))
            if span_key not in seen_spans:
                seen_spans.add(span_key)
                audience = _classify_audience(rel_path, context_msg)
                fp = _fingerprint(rel_path, i + 1, context_msg)
                entries.append(
                    DeprecationEntry(
                        source_file=rel_path,
                        line_number=i + 1,
                        mechanism="comment",
                        removal_version=cm.group("version"),
                        message=context_msg,
                        fingerprint=fp,
                        context_lines=_get_context(lines, i),
                        audience=audience,
                    )
                )

    for m in _DEPRECATED_TAG.finditer(text):
        line_no = text[: m.start()].count("\n") + 1
        nearby = " ".join(lines[max(0, line_no - 5) : line_no + 2])
        func_m = re.search(r"(?:def|class)\s+(\w+)", nearby)
        ctx_name = func_m.group(1) if func_m else "unknown"
        msg = f"Tag-based deprecation of {ctx_name}: removal in {m.group('version')}"
        _add(m.start(), "tag", m.group("version"), msg)

    return entries


# ── Existing rule inventory ──────────────────────────────────────────

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)
_RULE_ID_RE = re.compile(r"rule_id:\s*(\S+)")
_PY_RULE_ID_RE = re.compile(r'rule_id\s*[=:]\s*["\'](\w+)["\']')
_PY_DESC_RE = re.compile(r'description\s*[=:]\s*["\'](.+?)["\']')
_REGO_RULE_ID_RE = re.compile(r'"rule_id":\s*"(\w+)"')
_REGO_COMMENT_RE = re.compile(r"^#\s*\w+:\s*(.+)", re.MULTILINE)
_FM_DESC_RE = re.compile(r"description:\s*(.+)")


@dataclass
class RuleInfo:
    """Metadata about an existing APME rule.

    Attributes:
        rule_id: Rule identifier (e.g. L076, M009).
        path: File path relative to repo root.
        description: Rule description text.
        keywords: Distinctive keywords extracted from the rule.
    """

    rule_id: str
    path: str
    description: str = ""
    keywords: list[str] = field(default_factory=list)


def inventory_existing_rules() -> dict[str, RuleInfo]:
    """Scan OPA, native, and ansible rule dirs for existing rules.

    Returns:
        Map of rule_id -> RuleInfo.
    """
    rules: dict[str, RuleInfo] = {}

    def _add(rid: str, path: str, desc: str = "", source: str = "") -> None:
        kw = _extract_keywords(desc, source)
        if rid in rules:
            if desc and not rules[rid].description:
                rules[rid].description = desc
            if kw:
                rules[rid].keywords = list(set(rules[rid].keywords + kw))
        else:
            rules[rid] = RuleInfo(rule_id=rid, path=path, description=desc, keywords=kw)

    # OPA .rego files
    if OPA_BUNDLE.exists():
        for rego in OPA_BUNDLE.glob("*.rego"):
            if rego.name.endswith("_test.rego"):
                continue
            text = rego.read_text(encoding="utf-8", errors="replace")
            rel = str(rego.relative_to(REPO_ROOT))
            desc = ""
            cm = _REGO_COMMENT_RE.search(text)
            if cm:
                desc = cm.group(1).strip()
            for m in _REGO_RULE_ID_RE.finditer(text):
                _add(m.group(1), rel, desc, text)

        for md in OPA_BUNDLE.glob("*.md"):
            text = md.read_text(encoding="utf-8", errors="replace")
            fm = _FRONTMATTER_RE.match(text)
            if fm:
                rm = _RULE_ID_RE.search(fm.group(1))
                dm = _FM_DESC_RE.search(fm.group(1))
                if rm:
                    _add(rm.group(1), str(md.relative_to(REPO_ROOT)), dm.group(1) if dm else "", text)

    # Native .py files
    if NATIVE_RULES.exists():
        for py in NATIVE_RULES.glob("*.py"):
            text = py.read_text(encoding="utf-8", errors="replace")
            rel = str(py.relative_to(REPO_ROOT))
            desc = ""
            dm = _PY_DESC_RE.search(text)
            if dm:
                desc = dm.group(1).strip()
            for m in _PY_RULE_ID_RE.finditer(text):
                _add(m.group(1), rel, desc, text)

        for md in NATIVE_RULES.glob("*.md"):
            text = md.read_text(encoding="utf-8", errors="replace")
            fm = _FRONTMATTER_RE.match(text)
            if fm:
                rm = _RULE_ID_RE.search(fm.group(1))
                dm = _FM_DESC_RE.search(fm.group(1))
                if rm:
                    _add(rm.group(1), str(md.relative_to(REPO_ROOT)), dm.group(1) if dm else "")

    # Ansible validator rules
    if ANSIBLE_RULES.exists():
        for py in ANSIBLE_RULES.glob("*.py"):
            text = py.read_text(encoding="utf-8", errors="replace")
            rel = str(py.relative_to(REPO_ROOT))
            for m in _PY_RULE_ID_RE.finditer(text):
                _add(m.group(1), rel, "", text)
            for m in re.finditer(r'"rule_id":\s*"(\w+)"', text):
                _add(m.group(1), rel, "", text)

        for md in ANSIBLE_RULES.glob("*.md"):
            text = md.read_text(encoding="utf-8", errors="replace")
            fm = _FRONTMATTER_RE.match(text)
            if fm:
                rm = _RULE_ID_RE.search(fm.group(1))
                dm = _FM_DESC_RE.search(fm.group(1))
                if rm:
                    _add(rm.group(1), str(md.relative_to(REPO_ROOT)), dm.group(1) if dm else "")

    return rules


def _extract_keywords(description: str, source: str = "") -> list[str]:
    """Pull distinctive keywords from a rule's description and source.

    Args:
        description: Rule description text.
        source: Full source code of the rule file.

    Returns:
        List of matching signature keywords found in the text.
    """
    text = (description + " " + source).lower()
    keywords = []
    signatures = [
        "paramiko",
        "omap",
        "pairs",
        "vault-encrypted",
        "play_hosts",
        "follow_redirects",
        "first_found",
        "include_vars",
        "ignore_files",
        "empty when",
        "empty args",
        "action as",
        "action:",
        "strategy",
        "callback",
        "tree",
        "oneline",
        "k=v",
        "free_form",
        "_raw_params",
        "with_items",
        "with_dict",
        "deprecated module",
        "fqcn",
        "ansible_facts",
        "ansible_hostname",
        "variable name",
        "set_fact",
        "conditional",
        "jinja",
        "become",
        "no_log",
    ]
    for sig in signatures:
        if sig in text:
            keywords.append(sig)
    return keywords


# ── Gap analysis ─────────────────────────────────────────────────────


def _match_deprecation_to_rules(dep: DeprecationEntry, rules: dict[str, RuleInfo]) -> list[str]:
    """Return rule_ids of existing rules that likely cover this deprecation.

    Args:
        dep: A scraped deprecation entry to match.
        rules: Existing APME rule inventory keyed by rule_id.

    Returns:
        List of rule_id strings that appear to cover this deprecation.
    """
    matches = []
    dep_msg = dep.message.lower()
    dep_file = dep.source_file.lower()

    for rid, info in rules.items():
        # Match on overlapping keywords
        for kw in info.keywords:
            if kw in dep_msg or kw in dep_file:
                matches.append(rid)
                break
                # Match on description similarity
        if not any(r == rid for r in matches):
            desc = info.description.lower()
            if desc and len(desc) > 10:
                dep_words = set(dep_msg.split())
                desc_words = set(desc.split())
                common = dep_words & desc_words
                stop = {"the", "a", "an", "is", "in", "of", "to", "for", "and", "or", "not", "with", "be", "on", "at"}
                common -= stop
                if len(common) >= 3:
                    matches.append(rid)

    return matches


def build_gap_report(
    deprecations: list[DeprecationEntry],
    rules: dict[str, RuleInfo],
    commit: str,
    ansible_version: str,
) -> dict[str, Any]:
    """Compare scraped deprecations against existing rules and build a gap report.

    Args:
        deprecations: All scraped deprecation entries.
        rules: Existing APME rule inventory.
        commit: ansible/ansible commit hash that was scraped.
        ansible_version: ansible-core version string.

    Returns:
        Gap report dict with metadata and list of uncovered deprecations.
    """
    covered: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []

    for dep in deprecations:
        matching_rules = _match_deprecation_to_rules(dep, rules)
        entry = {
            "source_file": dep.source_file,
            "line_number": dep.line_number,
            "mechanism": dep.mechanism,
            "removal_version": dep.removal_version,
            "message": dep.message,
            "fingerprint": dep.fingerprint,
            "audience": dep.audience,
            "collection_name": dep.collection_name,
        }
        if matching_rules:
            entry["matched_rules"] = matching_rules
            covered.append(entry)
        else:
            entry["context_lines"] = dep.context_lines
            entry["rule_spec"] = _generate_rule_spec(dep)
            gaps.append(entry)

    # Deduplicate gaps by message similarity (many deprecations have the
    # same message scattered across multiple files)
    deduped_gaps = _deduplicate_gaps(gaps)

    return {
        "scraped_at": datetime.now(tz=UTC).isoformat(),
        "commit": commit,
        "ansible_core_version": ansible_version,
        "total_deprecations": len(deprecations),
        "covered_count": len(covered),
        "gap_count": len(deduped_gaps),
        "existing_rule_count": len(rules),
        "gaps": deduped_gaps,
    }


def _deduplicate_gaps(gaps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge gaps with identical or near-identical messages.

    Args:
        gaps: List of gap dicts to deduplicate by message similarity.

    Returns:
        Deduplicated list with additional ``other_locations`` for duplicates.
    """
    seen: dict[str, dict[str, Any]] = {}
    for gap in gaps:
        # Normalize: lowercase, strip whitespace, collapse spaces
        key = re.sub(r"\s+", " ", gap["message"].lower().strip())[:120]
        if key in seen:
            locs = seen[key].setdefault("other_locations", [])
            locs.append(f"{gap['source_file']}:{gap['line_number']}")
        else:
            seen[key] = gap
    return list(seen.values())


def _generate_rule_spec(dep: DeprecationEntry) -> dict[str, Any]:
    """Generate a detailed spec for a rule that would catch this deprecation.

    This spec is designed to be self-contained: a maintainer can use it
    directly to implement a new rule without needing to re-research the
    deprecation.

    Args:
        dep: The uncovered deprecation entry.

    Returns:
        Dict with title, severity, detection hints, and source context.
    """
    msg = dep.message
    ver = dep.removal_version

    # Determine severity based on removal timeline
    if ver.startswith("date:"):
        severity = "medium"
    else:
        try:
            parts = tuple(int(x) for x in ver.split("."))
            severity = "high" if parts <= (2, 22) else "medium"
        except (ValueError, TypeError):
            severity = "medium"

    # Generate a descriptive title
    title = _summarize_deprecation(msg)

    # Determine scope and what to look for
    detection_hints = _build_detection_hints(dep)

    return {
        "suggested_rule_id_prefix": "M",
        "title": title,
        "severity": severity,
        "removal_version": ver,
        "audience": dep.audience,
        "collection": dep.collection_name,
        "deprecation_message": msg,
        "source_location": f"{dep.source_file}:{dep.line_number}",
        "detection_mechanism": dep.mechanism,
        "detection_hints": detection_hints,
        "context": dep.context_lines,
    }


def _summarize_deprecation(message: str) -> str:
    """Create a concise title from a deprecation message.

    Args:
        message: Full deprecation message text.

    Returns:
        Shortened title suitable for issue headings (max ~100 chars).
    """
    msg = re.sub(r"\s+", " ", message.strip())
    # Truncate at common sentence boundaries
    for sep in [". ", "; ", " — ", " - "]:
        if sep in msg:
            msg = msg[: msg.index(sep)]
            break
    if len(msg) > 100:
        msg = msg[:97] + "..."
    return msg


def _build_detection_hints(dep: DeprecationEntry) -> dict[str, Any]:
    """Provide actionable detection guidance for rule implementers.

    Args:
        dep: The deprecation entry to analyze.

    Returns:
        Dict with scope, yaml_keys_to_check, yaml_patterns, and validator_recommendation.
    """
    msg_lower = dep.message.lower()
    file_lower = dep.source_file.lower()
    hints: dict[str, Any] = {}

    # Determine the YAML scope to check
    if "play" in file_lower or "playcall" in msg_lower:
        hints["scope"] = "play"
    elif "task" in file_lower or "executor" in file_lower:
        hints["scope"] = "task"
    elif "inventory" in file_lower:
        hints["scope"] = "inventory"
    else:
        hints["scope"] = "task"

    # Identify what YAML keys/values to look for
    yaml_keys: list[str] = []
    yaml_patterns: list[str] = []

    key_signals = {
        "when": ["when"],
        "action": ["action"],
        "args": ["args"],
        "connection": ["connection"],
        "strategy": ["strategy"],
        "follow_redirects": ["follow_redirects"],
        "ignore_files": ["ignore_files"],
        "include_vars": ["include_vars"],
        "callback": ["stdout_callback", "callbacks_enabled"],
        "!!omap": ["!!omap"],
        "!!pairs": ["!!pairs"],
        "!vault-encrypted": ["!vault-encrypted"],
        "play_hosts": ["play_hosts"],
        "ansible_hostname": ["ansible_*"],
        "paramiko": ["connection: paramiko_ssh"],
        "first_found": ["first_found"],
        "variable name": ["set_fact", "vars"],
    }
    for signal, keys in key_signals.items():
        if signal in msg_lower:
            yaml_keys.extend(keys)

    if "yes" in msg_lower and "no" in msg_lower:
        yaml_patterns.append("string boolean values (yes/no instead of true/false)")
    if "empty" in msg_lower:
        yaml_patterns.append("empty or null value for the key")
    if "k=v" in msg_lower or "key=value" in msg_lower:
        yaml_patterns.append("inline key=value arguments")

    hints["yaml_keys_to_check"] = yaml_keys or ["see deprecation message"]
    hints["yaml_patterns"] = yaml_patterns or ["see deprecation message"]
    hints["validator_recommendation"] = "opa" if yaml_keys else "native"

    return hints


# ── Markdown output ──────────────────────────────────────────────────


def format_issue_body(report: dict[str, Any]) -> str:
    """Format the gap report as a GitHub issue body in markdown.

    Args:
        report: Gap report dict from ``build_gap_report``.

    Returns:
        Markdown string suitable for a GitHub issue body, or empty if no gaps.
    """
    gaps = report["gaps"]
    if not gaps:
        return ""

    lines = [
        "## New Ansible-Core Deprecations Without APME Rules",
        "",
        f"**Scraped at**: {report['scraped_at']}",
        f"**Commit**: `{report['commit'][:12]}`",
        f"**ansible-core version**: {report['ansible_core_version']}",
        f"**Total deprecations found**: {report['total_deprecations']}",
        f"**Covered by existing rules**: {report['covered_count']}",
        f"**New gaps found**: {report['gap_count']}",
        "",
        "---",
        "",
    ]

    for i, gap in enumerate(gaps, 1):
        spec = gap.get("rule_spec", {})
        lines.append(f"### {i}. {spec.get('title', gap['message'][:80])}")
        lines.append("")
        lines.append(f"- **Removal version**: {gap['removal_version']}")
        lines.append(f"- **Severity**: {spec.get('severity', 'unknown')}")
        lines.append(f"- **Audience**: {gap['audience']}")
        lines.append(f"- **Source**: `{gap['source_file']}:{gap['line_number']}`")
        lines.append(f"- **Mechanism**: {gap['mechanism']}")
        lines.append("")

        lines.append("**Deprecation message**:")
        lines.append(f"> {gap['message']}")
        lines.append("")

        hints = spec.get("detection_hints", {})
        if hints:
            lines.append("**Detection guidance**:")
            lines.append(f"- Scope: `{hints.get('scope', 'task')}`")
            keys = hints.get("yaml_keys_to_check", [])
            if keys:
                lines.append(f"- YAML keys to check: {', '.join(f'`{k}`' for k in keys)}")
            patterns = hints.get("yaml_patterns", [])
            if patterns:
                for p in patterns:
                    lines.append(f"- Pattern: {p}")
            lines.append(f"- Recommended validator: `{hints.get('validator_recommendation', 'tbd')}`")
            lines.append("")

        ctx = gap.get("context_lines", [])
        if ctx:
            lines.append("<details><summary>Source context</summary>")
            lines.append("")
            lines.append("```python")
            for cl in ctx:
                lines.append(cl)
            lines.append("```")
            lines.append("</details>")
            lines.append("")

        other = gap.get("other_locations", [])
        if other:
            lines.append(f"<details><summary>Also found in {len(other)} other location(s)</summary>")
            lines.append("")
            for loc in other[:10]:
                lines.append(f"- `{loc}`")
            if len(other) > 10:
                lines.append(f"- ... and {len(other) - 10} more")
            lines.append("</details>")
            lines.append("")

        lines.append("---")
        lines.append("")

    lines.append("### Checklist")
    lines.append("")
    for i, gap in enumerate(gaps, 1):
        spec = gap.get("rule_spec", {})
        title = spec.get("title", gap["message"][:60])
        lines.append(f"- [ ] {i}. Create rule for: {title}")
    lines.append("")
    lines.append("---")
    lines.append("*Auto-generated by the deprecation-scrape workflow.*")

    return "\n".join(lines)


# ── Main ─────────────────────────────────────────────────────────────


def _version_gte(version: str, min_version: str) -> bool:
    try:
        v_parts = [int(x) for x in version.split(".")]
        mv_parts = [int(x) for x in min_version.split(".")]
        max_len = max(len(v_parts), len(mv_parts))
        v = tuple(v_parts + [0] * (max_len - len(v_parts)))
        mv = tuple(mv_parts + [0] * (max_len - len(mv_parts)))
        return v >= mv
    except (ValueError, TypeError):
        return True


def main() -> None:
    """CLI entry point: scrape, compare, and report gaps."""
    parser = argparse.ArgumentParser(
        description="Scrape ansible-core deprecations and identify gaps in APME rule coverage.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              python scripts/scrape_ansible_deprecations.py
              python scripts/scrape_ansible_deprecations.py --min-version 2.21 --audience content
              python scripts/scrape_ansible_deprecations.py --output-json gaps.json
        """),
    )
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE, help="Directory to clone ansible-core into")
    parser.add_argument("--branch", default="devel", help="Branch to scrape (default: devel)")
    parser.add_argument("--skip-clone", action="store_true", help="Skip git clone/fetch; use existing cache")
    parser.add_argument("--min-version", help="Only include deprecations >= this version (e.g. 2.21)")
    parser.add_argument(
        "--audience",
        choices=["content", "developer", "all"],
        default="all",
        help="Filter by audience (default: all)",
    )
    parser.add_argument("--output-json", type=Path, default=None, help="Write gap report JSON to this file")
    parser.add_argument("--output-md", type=Path, default=None, help="Write gap report markdown to this file")

    args = parser.parse_args()

    # Step 1: Clone/update ansible-core
    if not args.skip_clone:
        clone_or_update(args.cache_dir, args.branch)

    # Step 2: Scrape deprecations
    ansible_lib = args.cache_dir / ANSIBLE_LIB
    if not ansible_lib.exists():
        print(f"ERROR: {ansible_lib} not found", file=sys.stderr)
        sys.exit(1)

    commit = get_commit(args.cache_dir)
    version = get_ansible_version(args.cache_dir)
    all_entries: list[DeprecationEntry] = []

    py_files = sorted(ansible_lib.rglob("*.py"))
    print(f"Scanning {len(py_files)} Python files in {ansible_lib}…", file=sys.stderr)
    for py_file in py_files:
        all_entries.extend(scan_file(py_file, ansible_lib))
    print(f"Found {len(all_entries)} deprecation notices", file=sys.stderr)

    # Step 3: Filter
    filtered = [asdict(e) for e in all_entries]
    if args.min_version:
        filtered = [
            d
            for d in filtered
            if d["removal_version"].startswith("date:") or _version_gte(d["removal_version"], args.min_version)
        ]
    if args.audience != "all":
        filtered = [d for d in filtered if d["audience"] == args.audience]

    # Reconstruct entries for gap analysis
    entries = [DeprecationEntry(**{k: v for k, v in d.items()}) for d in filtered]
    print(f"After filtering: {len(entries)} deprecations", file=sys.stderr)

    # Step 4: Inventory existing rules
    rules = inventory_existing_rules()
    print(f"Found {len(rules)} existing rules in APME", file=sys.stderr)

    # Step 5: Build gap report
    report = build_gap_report(entries, rules, commit, version)
    print(f"Covered: {report['covered_count']}, Gaps: {report['gap_count']}", file=sys.stderr)

    # Step 6: Output
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"Wrote JSON report to {args.output_json}", file=sys.stderr)

    issue_body = format_issue_body(report)
    if args.output_md:
        args.output_md.parent.mkdir(parents=True, exist_ok=True)
        args.output_md.write_text(issue_body, encoding="utf-8")
        print(f"Wrote markdown report to {args.output_md}", file=sys.stderr)

    # Always write JSON to stdout for pipeline consumption
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
