"""AbbenayProvider — default AIProvider implementation using abbenay_grpc.

This is the sole file in the codebase that imports abbenay_grpc.
Install with: pip install apme-engine[ai]
"""

from __future__ import annotations

import json
import logging
import os
import re
from importlib.resources import files as pkg_files
from pathlib import Path

import yaml

from apme_engine.engine.models import ViolationDict
from apme_engine.remediation.ai_provider import AIPatch, AISkipped

logger = logging.getLogger(__name__)

_BEST_PRACTICES: dict[str, list[str]] | None = None

RULE_CATEGORY_MAP: dict[str, str] = {
    "M001": "fqcn",
    "M002": "fqcn",
    "M003": "fqcn",
    "M004": "fqcn",
    "L007": "yaml_formatting",
    "L008": "yaml_formatting",
    "L009": "yaml_formatting",
    "M006": "module_usage",
    "M008": "module_usage",
    "M009": "module_usage",
    "L011": "naming",
    "L012": "naming",
    "L013": "naming",
    "L043": "jinja2",
    "L046": "jinja2",
}

BATCH_PROMPT_TEMPLATE = """\
You are an Ansible remediation assistant. A static analysis tool has flagged
multiple issues in an Ansible YAML file. Fix ALL issues while following
Ansible best practices.

## Violations Found

{violation_list}

## File: {file_path} (line numbers shown as "N: content")
```yaml
{file_content}
```

## Ansible Best Practices
{best_practices}

{feedback_section}

## Instructions

For each violation, return a JSON object with a "patches" array.
Each patch replaces a range of lines (1-based, inclusive) in the original file.

Respond with ONLY this JSON (no markdown fences):
{{
  "patches": [
    {{
      "rule_id": "<the rule ID being fixed>",
      "line_start": <first line number to replace (1-based)>,
      "line_end": <last line number to replace (1-based, inclusive)>,
      "fixed_lines": "<the corrected YAML for just those lines>",
      "explanation": "<one-sentence explanation>",
      "confidence": 0.95
    }}
  ],
  "skipped": [
    {{
      "rule_id": "<the rule ID that could not be fixed>",
      "line": <line number of the violation>,
      "reason": "<1-2 sentences: why this could not be auto-fixed>",
      "suggestion": "<1-2 sentences: how the user can fix this manually>"
    }}
  ]
}}

Rules:
- Preserve all YAML comments
- Maintain exact indentation (2 spaces)
- Use FQCN for all modules (e.g., ansible.builtin.copy, not copy)
- Use YAML syntax for task arguments, not key=value
- Use true/false for booleans, not yes/no
- Each patch must cover only the task or block it fixes, not the whole file
- Multiple violations on the same task MUST be combined into ONE patch
- line_start and line_end must match the original file line numbers
- CRITICAL: fixed_lines must contain ONLY the replacement for lines line_start
  through line_end. Do NOT include lines before line_start or after line_end.
  Do NOT echo surrounding context — output ONLY the fixed range.
  Do NOT strip the "N: " line number prefix — output raw YAML only.
- CRITICAL: Do NOT include structural YAML keys (tasks:, handlers:, vars:,
  block:) in your patch UNLESS the key falls within your line_start:line_end
  range. If the key is outside your range, it will be preserved automatically.
  Including it will create duplicates and break the YAML.
- If you cannot fix a violation with confidence, add it to "skipped" instead
- Every violation must appear in either "patches" or "skipped"
"""


UNIT_PROMPT_TEMPLATE = """\
You are an Ansible remediation assistant. A static analysis tool has flagged
issues in a single task from an Ansible YAML file. Fix ALL issues while
following Ansible best practices.

## Violations Found

{violation_list}

## Task from {file_path} (lines {line_start}-{line_end}, shown as "N: content")
```yaml
{snippet}
```

## Ansible Best Practices
{best_practices}

{feedback_section}

## Instructions

Return a JSON object with a "patches" array and a "skipped" array.
Each patch replaces a range of lines (1-based, inclusive) using the ORIGINAL
file line numbers shown above.

Respond with ONLY this JSON (no markdown fences):
{{
  "patches": [
    {{
      "rule_id": "<the rule ID being fixed>",
      "line_start": <first line number to replace (1-based, from original file)>,
      "line_end": <last line number to replace (1-based, inclusive)>,
      "fixed_lines": "<the corrected YAML for just those lines>",
      "explanation": "<one-sentence explanation>",
      "confidence": 0.95
    }}
  ],
  "skipped": [
    {{
      "rule_id": "<the rule ID that could not be fixed>",
      "line": <line number of the violation>,
      "reason": "<1-2 sentences: why this could not be auto-fixed>",
      "suggestion": "<1-2 sentences: how the user can fix this manually>"
    }}
  ]
}}

Rules:
- line_start and line_end MUST use the original file line numbers ({line_start}-{line_end})
- Preserve all YAML comments
- Maintain exact indentation (2 spaces)
- Use FQCN for all modules (e.g., ansible.builtin.copy, not copy)
- Use YAML syntax for task arguments, not key=value
- Use true/false for booleans, not yes/no
- fixed_lines must contain ONLY the replacement for the specified line range
- If you cannot fix a violation with confidence, add it to "skipped" instead
- Every violation must appear in either "patches" or "skipped"
"""


def _build_unit_prompt(
    violations: list[ViolationDict],
    snippet: str,
    file_path: str,
    line_start: int,
    line_end: int,
    *,
    feedback: str | None = None,
) -> str:
    """Build LLM prompt for a single fixable unit (task).

    Args:
        violations: Violations scoped to this unit.
        snippet: YAML text of just this unit.
        file_path: Path to the file (for display).
        line_start: 1-based first line of the unit in the file.
        line_end: 1-based last line of the unit in the file.
        feedback: Optional feedback from a prior failed attempt.

    Returns:
        Formatted prompt string.
    """
    violation_entries: list[str] = []
    for idx, v in enumerate(violations, 1):
        rule_id = str(v.get("rule_id", ""))
        message = str(v.get("message", ""))
        line = _parse_line_value(v.get("line", 0))
        violation_entries.append(f"{idx}. [{rule_id}] line {line}: {message}")

    rule_ids = [str(v.get("rule_id", "")) for v in violations]
    best_practices = _get_best_practices_for_rules(rule_ids)

    feedback_section = ""
    if feedback:
        feedback_section = (
            f"## Previous Attempt Feedback\n{feedback}\n\nPlease correct these issues in your new response."
        )

    numbered_lines = [f"{i}: {line}" for i, line in enumerate(snippet.splitlines(), line_start)]
    numbered_snippet = "\n".join(numbered_lines)

    return UNIT_PROMPT_TEMPLATE.format(
        violation_list="\n".join(violation_entries),
        file_path=file_path,
        line_start=line_start,
        line_end=line_end,
        snippet=numbered_snippet,
        best_practices=best_practices,
        feedback_section=feedback_section,
    )


def discover_abbenay() -> str | None:
    """Auto-discover Abbenay daemon address from runtime socket.

    Checks XDG_RUNTIME_DIR/abbenay/daemon.sock first, then ~/.abbenay/daemon.sock.

    Returns:
        A 'unix://' address string, or None if no socket found.
    """
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    candidates: list[Path] = []
    if xdg:
        candidates.append(Path(xdg) / "abbenay" / "daemon.sock")
    candidates.append(Path.home() / ".abbenay" / "daemon.sock")
    for sock in candidates:
        if sock.exists():
            return f"unix://{sock}"
    return None


def _load_best_practices() -> dict[str, list[str]]:
    """Load the structured best practices mapping from the data package.

    Returns:
        Dict keyed by category with lists of guideline strings.
    """
    global _BEST_PRACTICES  # noqa: PLW0603
    if _BEST_PRACTICES is not None:
        return _BEST_PRACTICES

    data_dir = pkg_files("apme_engine") / "data"
    bp_path = data_dir / "ansible_best_practices.yml"
    raw = bp_path.read_text(encoding="utf-8")
    loaded = yaml.safe_load(raw)
    loaded.pop("_meta", None)
    _BEST_PRACTICES = loaded
    return _BEST_PRACTICES


def _get_best_practices_for_rules(rule_ids: list[str]) -> str:
    """Return formatted best practices for a set of rule categories.

    Args:
        rule_ids: List of APME rule IDs.

    Returns:
        Formatted string of relevant guidelines.
    """
    bp = _load_best_practices()
    universal = bp.get("universal", [])

    categories: set[str] = set()
    for rid in rule_ids:
        bare = rid.split(":")[-1] if ":" in rid else rid
        cat = RULE_CATEGORY_MAP.get(bare, "")
        if cat:
            categories.add(cat)

    specific: list[str] = []
    for cat in sorted(categories):
        specific.extend(bp.get(cat, []))

    combined = universal + specific
    if not combined:
        return "No specific guidelines available."
    seen: set[str] = set()
    deduped: list[str] = []
    for g in combined:
        if g not in seen:
            seen.add(g)
            deduped.append(g)
    return "\n".join(f"- {g}" for g in deduped)


def _parse_line_value(line_val: object) -> int:
    """Extract an integer line number from a violation line field.

    Args:
        line_val: Line value which may be int, str like "L19-25", or other.

    Returns:
        Best-effort integer line number, or 0.
    """
    if isinstance(line_val, int):
        return line_val
    if isinstance(line_val, str):
        match = re.search(r"\d+", line_val)
        return int(match.group()) if match else 0
    return 0


def _build_batch_prompt(
    violations: list[ViolationDict],
    file_content: str,
    file_path: str = "",
    *,
    feedback: str | None = None,
) -> str:
    """Build LLM prompt for batch remediation of a single file.

    Args:
        violations: All violations for this file.
        file_content: Full file content.
        file_path: Path to the file (for display).
        feedback: Optional feedback from a prior failed attempt.

    Returns:
        Formatted prompt string.
    """
    violation_entries: list[str] = []
    for idx, v in enumerate(violations, 1):
        rule_id = str(v.get("rule_id", ""))
        message = str(v.get("message", ""))
        line = _parse_line_value(v.get("line", 0))
        violation_entries.append(f"{idx}. [{rule_id}] line {line}: {message}")

    rule_ids = [str(v.get("rule_id", "")) for v in violations]
    best_practices = _get_best_practices_for_rules(rule_ids)

    feedback_section = ""
    if feedback:
        feedback_section = (
            f"## Previous Attempt Feedback\n{feedback}\n\nPlease correct these issues in your new response."
        )

    numbered_lines = [f"{i}: {line}" for i, line in enumerate(file_content.splitlines(), 1)]
    numbered_content = "\n".join(numbered_lines)

    return BATCH_PROMPT_TEMPLATE.format(
        violation_list="\n".join(violation_entries),
        file_path=file_path,
        file_content=numbered_content,
        best_practices=best_practices,
        feedback_section=feedback_section,
    )


def _parse_batch_response(
    response_text: str,
    file_content: str,
    *,
    min_line_override: int | None = None,
    max_line_override: int | None = None,
) -> tuple[list[AIPatch] | None, list[AISkipped]]:
    """Parse the LLM batch JSON response into patches and skipped entries.

    Args:
        response_text: Raw text response from the LLM.
        file_content: Original file content (for line range validation).
        min_line_override: If provided, use this as the minimum valid line number.
            Used for unit-level parsing where patches must stay within the unit's
            line range.
        max_line_override: If provided, use this as the max valid line number
            instead of counting lines in file_content.  Used for unit-level
            parsing where file_content is just the snippet but line numbers
            refer to the original file.

    Returns:
        Tuple of (patches or None on failure, skipped violations).
    """
    cleaned = response_text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines)

    try:
        data = json.loads(cleaned)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Failed to parse batch AI response as JSON")
        return None, []

    raw_patches = data.get("patches")
    if not isinstance(raw_patches, list):
        logger.warning("AI response missing or invalid 'patches' field")
        return None, []

    min_line = min_line_override if min_line_override else 1
    max_line = max_line_override if max_line_override else len(file_content.splitlines())
    result: list[AIPatch] = []

    for entry in raw_patches:
        if not isinstance(entry, dict):
            continue
        rule_id = str(entry.get("rule_id", ""))
        line_start = entry.get("line_start")
        line_end = entry.get("line_end")
        fixed_lines = entry.get("fixed_lines")
        explanation = str(entry.get("explanation", ""))
        confidence = float(entry.get("confidence", 0.0))

        if not all([rule_id, line_start is not None, line_end is not None, fixed_lines is not None]):
            logger.warning("Skipping malformed patch entry: %s", entry)
            continue

        ls = int(line_start)  # type: ignore[arg-type]
        le = int(line_end)  # type: ignore[arg-type]
        if ls < min_line or le < ls or ls > max_line:
            logger.warning(
                "Skipping patch with invalid line range %d-%d (valid range %d-%d)",
                ls,
                le,
                min_line,
                max_line,
            )
            continue

        le = min(le, max_line)

        fixed_str = str(fixed_lines)

        result.append(
            AIPatch(
                rule_id=rule_id,
                line_start=ls,
                line_end=le,
                fixed_lines=fixed_str,
                explanation=explanation,
                confidence=confidence,
            )
        )

    skipped = _parse_skipped(data)

    if not result:
        logger.warning("No valid patches in AI response")
        return None, skipped

    return result, skipped


def _parse_skipped(data: dict) -> list[AISkipped]:  # type: ignore[type-arg]
    """Extract skipped violations from the parsed LLM JSON.

    Args:
        data: Parsed JSON response dict.

    Returns:
        List of AISkipped objects (empty if none present).
    """
    raw = data.get("skipped")
    if not isinstance(raw, list):
        return []

    result: list[AISkipped] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        rule_id = str(entry.get("rule_id", ""))
        line = entry.get("line", 0)
        reason = str(entry.get("reason", ""))
        suggestion = str(entry.get("suggestion", ""))
        if rule_id and (reason or suggestion):
            result.append(
                AISkipped(
                    rule_id=rule_id,
                    line=int(line) if line else 0,
                    reason=reason,
                    suggestion=suggestion,
                )
            )
    return result


# Keep the old single-violation helpers for backward compatibility in tests
def _get_best_practices_for_rule(rule_id: str) -> str:
    """Return formatted best practices for a single rule's category.

    Args:
        rule_id: The APME rule ID (e.g. 'M001', 'L007').

    Returns:
        Formatted string of relevant guidelines.
    """
    return _get_best_practices_for_rules([rule_id])


def _extract_code_window(
    file_content: str,
    line: int,
    context: int = 10,
) -> tuple[str, int, int]:
    """Extract a window of lines around the violation.

    Args:
        file_content: Full file content.
        line: 1-based line number of the violation.
        context: Number of lines of context before and after.

    Returns:
        Tuple of (code_window, start_line, end_line).
    """
    lines = file_content.splitlines()
    start = max(0, line - 1 - context)
    end = min(len(lines), line + context)
    window = "\n".join(lines[start:end])
    return window, start + 1, end


class AbbenayProvider:
    """AIProvider implementation using the Abbenay daemon via abbenay_grpc.

    This is the sole file that imports abbenay_grpc. The import is
    deferred to __init__ so the core package works without it installed.
    """

    def __init__(
        self,
        addr: str,
        *,
        token: str | None = None,
        model: str | None = None,
    ) -> None:
        """Initialize the Abbenay provider.

        Args:
            addr: Daemon address (e.g. 'unix:///run/user/1000/abbenay/daemon.sock').
            token: Optional consumer auth token for inline policy access.
            model: Optional default model (e.g. 'openai/gpt-4o').

        Raises:
            ImportError: If abbenay_grpc is not installed.
        """
        try:
            from abbenay_grpc import AbbenayClient  # noqa: PLC0415
        except ImportError:
            raise ImportError(
                "AI escalation requires the 'ai' extra.\nInstall with: pip install apme-engine[ai]"
            ) from None

        if addr.startswith("unix://"):
            socket_path = addr.removeprefix("unix://")
            self._client: object = AbbenayClient(socket_path=socket_path)
        elif ":" in addr:
            host, _, port_str = addr.rpartition(":")
            self._client = AbbenayClient(host=host, port=int(port_str))
        else:
            self._client = AbbenayClient(host=addr)
        self._addr = addr
        self._token = token
        self._model = model
        self._AbbenayClient = AbbenayClient

    def _make_client(self) -> object:
        """Create a fresh AbbenayClient instance for the current event loop.

        Returns:
            New AbbenayClient bound to the current asyncio loop.
        """
        addr = self._addr
        if addr.startswith("unix://"):
            return self._AbbenayClient(socket_path=addr.removeprefix("unix://"))
        if ":" in addr:
            host, _, port_str = addr.rpartition(":")
            return self._AbbenayClient(host=host, port=int(port_str))
        return self._AbbenayClient(host=addr)

    async def preflight(self) -> bool:
        """Connect to the daemon and run a health check.

        Returns:
            True if the daemon is healthy, False otherwise.
        """
        try:
            self._client = self._make_client()
            await self._client.connect()  # type: ignore[attr-defined]
            result: bool = await self._client.health_check()  # type: ignore[attr-defined]
            return result
        except Exception:
            logger.exception("Abbenay health check failed")
            return False

    async def reconnect(self) -> None:
        """Recreate client and reconnect for the current event loop."""
        self._client = self._make_client()
        await self._client.connect()  # type: ignore[attr-defined]

    async def propose_fixes(
        self,
        violations: list[ViolationDict],
        file_content: str,
        *,
        model: str | None = None,
        feedback: str | None = None,
    ) -> tuple[list[AIPatch] | None, list[AISkipped]]:
        """Propose fixes for all violations in a file via a single LLM call.

        Args:
            violations: All violations for this file.
            file_content: Full content of the file.
            model: Optional model override for this request.
            feedback: Validation failure context for retry attempts.

        Returns:
            Tuple of (patches or None on failure, skipped violations).
        """
        file_path = str(violations[0].get("file", "")) if violations else ""
        prompt = _build_batch_prompt(
            violations,
            file_content,
            file_path,
            feedback=feedback,
        )
        effective_model = model or self._model

        policy: dict[str, object] = {
            "sampling": {"temperature": 0.0},
            "output": {
                "format": "json_only",
                "max_tokens": 32768,
            },
            "reliability": {
                "timeout": 120000,
            },
        }

        try:
            response_text = ""
            async for chunk in self._client.chat(  # type: ignore[attr-defined]
                model=effective_model or "",
                message=prompt,
                policy=policy,
                token=self._token,
            ):
                if hasattr(chunk, "text") and chunk.text:
                    response_text += chunk.text
        except Exception:
            logger.exception(
                "Abbenay batch call failed for %d violations in %s",
                len(violations),
                file_path,
            )
            return None, []

        if not response_text.strip():
            logger.warning(
                "Empty response from Abbenay for %d violations in %s",
                len(violations),
                file_path,
            )
            return None, []

        return _parse_batch_response(response_text, file_content)

    async def propose_unit_fixes(
        self,
        violations: list[ViolationDict],
        snippet: str,
        file_path: str,
        line_start: int,
        line_end: int,
        *,
        model: str | None = None,
        feedback: str | None = None,
    ) -> tuple[list[AIPatch] | None, list[AISkipped]]:
        """Propose fixes for violations within a single fixable unit.

        Sends only the unit snippet (not the full file) to the LLM,
        reducing token usage and improving fix quality.

        Args:
            violations: Violations scoped to this unit.
            snippet: YAML text of just this unit.
            file_path: Path to the file (for display).
            line_start: 1-based first line of the unit in the file.
            line_end: 1-based last line of the unit in the file.
            model: Optional model override.
            feedback: Validation failure context for retry.

        Returns:
            Tuple of (patches or None on failure, skipped violations).
        """
        prompt = _build_unit_prompt(
            violations,
            snippet,
            file_path,
            line_start,
            line_end,
            feedback=feedback,
        )
        effective_model = model or self._model

        policy: dict[str, object] = {
            "sampling": {"temperature": 0.0},
            "output": {
                "format": "json_only",
                "max_tokens": 8192,
            },
            "reliability": {
                "timeout": 60000,
            },
        }

        try:
            response_text = ""
            async for chunk in self._client.chat(  # type: ignore[attr-defined]
                model=effective_model or "",
                message=prompt,
                policy=policy,
                token=self._token,
            ):
                if hasattr(chunk, "text") and chunk.text:
                    response_text += chunk.text
        except Exception:
            logger.exception(
                "Abbenay unit call failed for %d violations (lines %d-%d) in %s",
                len(violations),
                line_start,
                line_end,
                file_path,
            )
            return None, []

        if not response_text.strip():
            return None, []

        return _parse_batch_response(response_text, snippet, min_line_override=line_start, max_line_override=line_end)
