# Research: Ansible Playbook Modernization Engine

**Date**: 2026-03-04
**Feature**: 001-ansible-modernization-engine

## R1: ARI Integration Strategy

**Decision**: Use ARI as an installed Python package dependency (already in `pyproject.toml` as `ansible-risk-insight>=0.2.10`).

**Rationale**:
- ARI is already declared as a dependency in x2a-convertor's `pyproject.toml`
- It has a clean programmatic Python API via `ARIScanner` class - no need to shell out to CLI
- All ARI dependencies (jsonpickle, PyYAML, ruamel.yaml, requests, rapidfuzz, joblib, filelock, gitdb, smmap) are compatible with x2a-convertor's existing deps
- The local `ansible-risk-insight/` directory serves as reference code for custom rule development

**Alternatives Considered**:
- Git submodule: Unnecessary complexity since it's already a pip dependency
- Vendoring source code: Maintenance burden, no benefit over pip install
- Forking ARI: Would fragment from upstream; only needed if we require breaking changes

## R2: ARI Programmatic API Surface

**Decision**: Use `ARIScanner` class with `Config` for all scanning operations.

**Key API** (code-verified against `scanner.py` and `models.py` in `ansible-risk-insight/`):
```python
from ansible_risk_insight import ARIScanner, Config

scanner = ARIScanner(Config(
    rules_dir="/path/to/custom/rules",  # Colon-separated dirs
    rules=["P001", "P002", "R301"],     # Filter to specific rules
    data_dir="/tmp/ari-data",           # Cache directory
))

# For full project scanning (playbooks + roles + collections):
result = scanner.evaluate(
    type="project",         # Scans ALL playbooks, roles, and modules
    path="/path/to/project",
    # playbook_only defaults to False - DO NOT set True for project scans
)

# Access results
# Note: rule_result.rule is of type RuleMetadata (Rule inherits from RuleMetadata)
# Note: rule_result.detail is a dict, not a string
# Note: rule_result.file is a tuple (file_path, line_range_str)
for target in result.targets:
    for node in target.nodes:
        for rule_result in node.rules:
            if rule_result.verdict:
                file_path, line_range = rule_result.file  # e.g. ("/abs/path/main.yml", "L12-18")
                print(f"{file_path} {line_range} [{rule_result.rule.rule_id}]: {rule_result.detail}")
```

**Result hierarchy**: `ARIResult` → `TargetResult` → `NodeResult` → `RuleResult`

**Verified class definitions** (from `models.py`):

| Class | Location | Key Attributes |
|-------|----------|----------------|
| `ARIResult` | `models.py:2662` | `targets: List[TargetResult]`; helper methods: `playbooks()`, `roles()`, `taskfiles()` |
| `TargetResult` | `models.py:2593` | `target_type: str` (playbook/role/taskfile), `target_name: str`, `nodes: List[NodeResult]`; helper: `matched_rules()` returns rules with `verdict=True` |
| `NodeResult` | `models.py:2543` | `node: RunTarget`, `rules: List[RuleResult]`; helper: `search_results(rule_id=, tag=, matched=, verdict=)` |
| `RuleResult` | `models.py:2458` | `rule: RuleMetadata`, `verdict: bool`, `detail: dict`, `file: tuple`, `error: str`, `matched: bool`, `duration: float` |

**`Config` class** (from `scanner.py:77`):

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `path` | str | `""` | Path to config file |
| `data_dir` | str | `""` | ARI cache directory |
| `rules_dir` | str | `""` | Colon-separated custom rule directories |
| `logger_key` | str | `""` | Logger identifier |
| `log_level` | str | `""` | Logging level |
| `rules` | list | `[]` | Rule IDs to enable |
| `disable_default_rules` | bool | `False` | If True, skip built-in rules |

### Critical API Behaviors (verified from source):

**`playbook_only` parameter**:
- `True`: Calls `load_playbook()` - loads ONLY the specific playbook, suppresses role/collection traversal
- `False` (default): Calls `load_repository()` - loads playbooks, roles, AND modules
- Auto-set to `True` when `playbook_yaml` parameter is passed (in-memory scanning)
- **For APME: always use `type="project"` with `playbook_only=False`**

**`spec_mutation` behavior**:
- Mutations happen to **in-memory specs only**, NOT files on disk
- `apply_spec_mutations()` overwrites entries in the in-memory `root_definitions` dictionary
- File-on-disk modification requires a **separate explicit call** to `update_the_yaml_target(file_path, line_number_list, new_content_list)` from `ansible_risk_insight.finder` (verified at `finder.py:989`)
  - `line_number_list`: list of line range strings using "L" prefix (e.g., `["L6-13"]` — note: the format is `L<start>-<end>`, NOT `L6-L13`). Internally parsed via `line_number.lstrip("L").split("-")` to extract start and end integers.
  - `new_content_list`: list of replacement YAML content strings, one per entry in `line_number_list`
  - **The `file` attribute on `RuleResult`** returns a tuple `(file_path, line_range_str)` in exactly this format (e.g., `("path/to/file.yml", "L12-18")`), so `RuleResult.file[1]` can be passed directly to `line_number_list`.
- **For APME: after scanning, we must explicitly apply mutations to copies in the output directory**

**Auto-rescan after mutation**:
- **Automatic within `evaluate()`** - it recursively calls itself with `spec_mutations_from_previous_scan`
- Includes loop detection: compares previous mutations with current via `equal()`, warns and exits if identical mutations repeat
- The caller receives the **final result** after all mutation passes complete - no need to call `evaluate()` again

**Pass termination algorithm** (for APME's `--max-passes` control):

ARI handles internal mutation-rescan loops automatically within a single `evaluate()` call. APME's `--max-passes` controls a higher-level loop: scan → apply fixes to output files → rescan output files. The algorithm:

**Pre-loop setup** (rewrite only):
0. Copy the source directory tree to the output directory. All subsequent scanning and fixing operates on the **output directory copies only**. The original source directory is never scanned by ARI — it is only used as the baseline for diff/patch generation after all passes complete.

**Multi-pass loop**:
1. `pass_count = 0`, `previous_auto_fixable_count = infinity`
2. Run `ARIScanner.evaluate()` on the **output directory** files
3. Count auto-fixable findings (findings where `is_finding_resolvable()` returns `True`)
4. If `auto_fixable_count == 0` → **converged**, exit loop
5. If `auto_fixable_count >= previous_auto_fixable_count` → **oscillation detected**, log warning, exit loop (prevents infinite loops where fixes create new issues)
6. Apply fixes to output files via `update_the_yaml_target()`
7. `pass_count += 1`, `previous_auto_fixable_count = auto_fixable_count`
8. If `pass_count >= max_passes` → **limit reached**, exit loop with remaining findings reported
9. Go to step 2

**Scan command**: The scan command does NOT copy files or use an output directory for scanning. It runs `ARIScanner.evaluate()` directly on the **source directory** (read-only). The `--output-dir` for scan only determines where the report file is written.

**Identifying unresolvable findings**:
- **No explicit flag** on `RuleResult` distinguishes resolved from unresolved
- A finding is "resolved" if its `verdict` becomes `False` after mutation passes, or if it has a `spec.mutations` annotation (set by `callobj.set_annotation(key="spec.mutations", value=value, rule_id=rule_id)` — note the 3-parameter signature)
- A finding is "unresolvable" if `verdict=True` persists in the final `ARIResult` AND the finding's rule does NOT have `spec_mutation=True`
- **For APME pipeline**: partition final results - findings from `spec_mutation` rules with `verdict=False` = fixed; all remaining `verdict=True` = escalate to AI

**Critical architectural requirement — `is_finding_resolvable()`**: The deterministic-vs-AI partition (the core of the two-tier pipeline in R4) depends on inferring resolvability from ARI internals. This inference MUST be isolated in a single function:

```python
# src/scanning/scan_service.py
def is_finding_resolvable(rule_result: RuleResult) -> bool:
    """Determine if ARI can auto-fix this finding via spec_mutation.

    Returns True if the rule has spec_mutation=True capability,
    meaning ARI can generate a fix. Returns False for rules that
    only detect issues without providing mutations.

    This is the SOLE decision point for the deterministic/AI partition.
    """
    return getattr(rule_result.rule, 'spec_mutation', False)
```

Note: `rule_result.rule` is typed as `RuleMetadata`, but at runtime for ARI's built-in rules it is a `Rule` instance (which inherits from `RuleMetadata` and adds `spec_mutation`). For custom rules, the `Rule` base class also defines `spec_mutation`. The `getattr` with default `False` safely handles both cases.

This function is the **sole decision point** for partitioning findings into `deterministic_fixes` vs. `complex_findings` in ScanState. All partitioning logic flows through it.

**Fragility note and mitigation**: The above inference logic (checking `verdict` + `spec_mutation` flag) depends on ARI internal implementation details that could change between versions. To mitigate:
1. **Isolate the detection**: Already mandated above — `is_finding_resolvable()` in `src/scanning/scan_service.py`.
2. **Unit test against known ARI behavior**: Write tests with fixtures that produce both resolvable and unresolvable findings, asserting correct partitioning. These tests will fail fast if an ARI upgrade changes the behavior.
3. **Pin ARI version**: The current `pyproject.toml` uses `ansible-risk-insight>=0.2.10` without an upper bound. When upgrading ARI, review the partitioning logic in `is_finding_resolvable()` to ensure `spec_mutation` behavior has not changed.

### RuleResult → Finding Conversion (code-verified)

Converting ARI `RuleResult` objects to APME `Finding` entities is a critical integration boundary. All conversion logic MUST be isolated in a single function in `scan_service.py`. The exact attribute-access patterns (verified against ARI source):

```python
# src/scanning/scan_service.py
def rule_result_to_finding(
    rule_result: RuleResult,
    finding_id: str,
    rule_category_map: dict[str, FindingCategory],
    severity_map: dict[str, Severity],
    source_dir: str,
) -> Finding:
    """Convert an ARI RuleResult to an APME Finding.

    Attribute access patterns verified against ARI models.py.
    """
    # --- File path and line number ---
    # RuleResult.file is a tuple: (absolute_file_path, line_range_str)
    # line_range_str format: "L<start>-<end>" (e.g., "L12-18") or "?" if unknown
    # Set by RunTarget.file_info() → calls self.spec.defined_in and self.spec.line_number
    # Verified at models.py:1702-1708 (TaskCall.file_info)
    file_path, line_range = rule_result.file
    # Make path relative to source_dir for report output
    rel_path = os.path.relpath(file_path, source_dir) if file_path else ""
    # Parse line number from range string
    start_line = 0
    if line_range and line_range != "?":
        line_parts = line_range.lstrip("L").split("-")
        start_line = int(line_parts[0])

    # --- Rule metadata ---
    # rule_result.rule is typed RuleMetadata but at runtime is Rule (inherits RuleMetadata)
    # RuleMetadata fields: rule_id, description, name, version, commit_id, severity, tags
    # Rule adds: enabled, precedence, spec_mutation
    rule_id = rule_result.rule.rule_id        # e.g., "R301", "P001", "SEC001"
    rule_severity = rule_result.rule.severity  # lowercase string: "error", "warning", "info"

    # --- Description from detail dict ---
    # RuleResult.detail is a dict, NOT a string. ARI built-in rules use
    # {"message": "..."} format. Custom rules MUST follow the same convention.
    # Example from R301: {"message": "Non-FQCN module 'apt'", ...}
    description = ""
    if rule_result.detail:
        description = rule_result.detail.get("message", str(rule_result.detail))

    # --- Suggested fix ---
    # Not directly on RuleResult. For spec_mutation rules, the fix is implicit
    # (ARI applies it via mutation). For display, derive from rule context:
    # - R301: "Use '<fqcn>'" (from detail dict, which often includes the FQCN)
    # - P002: "'<old_param>' renamed to '<new_param>'" (from detail)
    # - SEC*/AAP*/EE*: Custom rules set their suggested fix in detail["suggested_fix"]
    suggested_fix = rule_result.detail.get("suggested_fix") if rule_result.detail else None

    # --- Category and severity mapping ---
    category = rule_category_map.get(rule_id, FindingCategory.VERSION)
    severity = severity_map.get(rule_severity, Severity.HINT)

    # --- Auto-fixable determination ---
    auto_fixable = is_finding_resolvable(rule_result)

    return Finding(
        finding_id=finding_id,
        rule_id=rule_id,
        severity=severity,
        category=category,
        description=description,
        file_path=rel_path,
        line_number=start_line,
        suggested_fix=suggested_fix,
        auto_fixable=auto_fixable,
        fix_applied=False,
        fix_source=None,
    )
```

**Key ARI source chain for file/line info** (verified):
1. `RuleResult.file` → set by the rule's `process()` method, e.g., `file=task.file_info()`
2. `TaskCall.file_info()` (`models.py:1702`) → reads `self.spec.defined_in` (file path) and `self.spec.line_number` (list of `[start, end]`)
3. `Task.line_number` (`models.py:1453`) → property returning `self.line_num_in_file` (set during YAML parsing via `set_yaml_lines()`)
4. Returns tuple: `(file_path_str, f"L{start}-{end}")`

**Custom rule convention for `detail` dict**: All APME custom rules (SEC*, AAP*, EE*) MUST include `"message"` in their `detail` dict, and MAY include `"suggested_fix"` for display. Example:
```python
return RuleResult(
    verdict=True,
    detail={
        "message": "Hardcoded password detected in 'db_password'",
        "suggested_fix": "Migrate to Ansible Vault",
    },
    file=task.file_info(),
    rule=self.get_metadata(),
)
```

This convention ensures `rule_result_to_finding()` can extract description and suggested fix uniformly from both ARI built-in and APME custom rules. ARI built-in rules already use `"message"` as the primary detail key.

## R3: x2a-convertor Integration Points

**Decision**: Follow existing patterns - new `src/scanning/` module with agent, state, and service classes.

**CLI pattern** (from `app.py`):
- Click commands with `@cli.command()`, `@handle_exceptions`, `@click.option("--source-dir", ...)`
- Add `scan` and `rewrite` commands following existing `validate` command pattern

**Agent pattern** (from `src/base_agent.py`):
- Extend `BaseAgent[ScanState]` with `execute()` method
- Use `invoke_react()` for ReAct-style agent loops with LangChain tools (supplementary; not the primary AI escalation path)
- Use `invoke_structured()` for structured LLM output with Pydantic validation (used by ScanAgent for AISuggestion generation)
- Use `invoke_llm()` for unstructured LLM calls (not used by ScanAgent)

**Validation pattern** (from `src/validation/`):
- `ValidationService` with list of validators
- `ValidationResult(success, message, validator_name)` dataclass
- Create `ARIValidator` following `AnsibleLintValidator` pattern

**Tool pattern** (from `tools/base_tool.py`):
- Extend `X2ATool` with `_run()` method
- Create `ARIScanTool` wrapping `ARIScanner` for agent use

**Config pattern** (from `src/config/settings.py`):
- Pydantic `BaseSettings` with env var support
- Add `ARISettings` class for ARI-specific config

### ARI vs ansible-lint Rule Coverage (code-verified)

ARI and ansible-lint serve **complementary purposes** - ARI does NOT replace ansible-lint:

| Rule | ARI Coverage | ansible-lint Coverage |
|------|-------------|----------------------|
| P001 (module name validation) | `P001_module_name_validation.py` | Not covered |
| P002 (argument key validation) | `P002_module_argument_key_validation.py` | Not covered |
| P003 (argument value validation) | `P003_module_argument_value_validation.py` | Not covered |
| P004 (variable validation) | `P004_variable_validation.py` | Not covered |
| R301 (non-FQCN use) | `R301_non_fqcn_use.py` | Not covered |
| R303 (task without name) | `R303_task_without_name.py` | `name[missing]` |
| R116 (insecure file permissions) | `R116_insecure_file_permission.py` (disabled by default) | `risky-file-permissions` |
| YAML syntax/load errors | Not covered | `load-failure`, `syntax-check`, `parser-error` |
| General code quality | Not covered | `no-changed-when`, `command-instead-of-module`, etc. |

**For APME**: Use ARI as the primary scanning engine. The existing `AnsibleLintValidator` remains in the migrate/export workflow but is NOT used by the new scan/rewrite commands. No ansible-lint rules need to be ported to ARI.

**R116 activation**: R116 (insecure file permissions) is disabled by default in ARI. APME explicitly enables it by including `"R116"` in the `ARIScanner(Config(rules=[...]))` rule list alongside other active rules. This ensures SECURITY findings for insecure file permissions are included in scan results.

**ARIValidator vs ARIScanTool clarification**:
- `ARIValidator` (in `src/validation/ari_validator.py`): **Low-priority / nice-to-have.** A thin wrapper implementing the `Validator` interface so ARI can optionally participate in the existing `ValidationService` pipeline used by the `validate` command. It delegates to `ARIScanner` internally. This is for users who want ARI checks as part of the existing `app.py validate` workflow. **No user story or functional requirement covers this integration** — it is an optional enhancement that can be deferred without affecting any acceptance scenario. If implemented, it should be a separate task after the core scan/rewrite/apply commands are complete.
- `ARIScanTool` (in `tools/ari_scan.py`): A `X2ATool` subclass that wraps `ARIScanner` for use by the LangGraph `ScanAgent` during AI escalation. The agent has ARIScanTool available as an optional tool if it needs to re-scan a specific file to gather more context, but the primary AI escalation path is via `invoke_structured()` with pre-packaged finding context (see R4). ARIScanTool is a supplementary capability, not the main analysis path.
- `ScanService` (in `src/scanning/scan_service.py`): The main orchestrator for `scan` and `rewrite` commands. Calls `ARIScanner` directly — it does NOT go through `ValidationService` or `ARIValidator`. This is the primary code path.

## R4: Two-Tier Pipeline Architecture

**Decision**: Deterministic ARI scan first, then automatic LLM escalation for unresolved findings.

**Pipeline flow**:
1. **[scan]** Run `ARIScanner.evaluate()` on the **source directory** (read-only). **[rewrite]** Copy source tree to output directory, then run `ARIScanner.evaluate()` on the **output directory** copy.
2. Collect all `RuleResult` objects where `verdict=True`
3. Partition findings:
   - **Deterministic fixes**: Findings where ARI's spec_mutation rules can auto-fix (FQCN, parameter renames)
   - **Complex findings**: Findings flagged but not auto-fixable (complex Jinja2, custom module logic)
4. **[rewrite only]** Apply deterministic fixes to output directory files via `update_the_yaml_target()`. Run multi-pass loop (see R2 pass termination algorithm) until converged or `--max-passes` reached.
5. **[rewrite + AI enabled only]** Pass complex findings to `ScanAgent` (LangGraph) for AI analysis. Skipped when `--no-ai` is active (complex findings get `fix_source: "manual"` directly) or in scan mode.
6. **[rewrite only]** Generate diff/patch files by comparing original source directory against corrected output directory.
7. Merge results into the output report (findings + any AI suggestions). Label each fix as "deterministic", "AI-suggested", or "manual".

**Mode-specific behavior**:
- **`scan` command**: Steps 1-3 + 7 only. Scans source directly (read-only). No fixes applied, no AI escalation. All findings have `fix_applied: false` and `fix_source: null`.
- **`rewrite` command**: All steps. Source copied to output dir first. Deterministic fixes applied in step 4. Complex findings escalated in step 5.
- **`rewrite --no-ai`**: Steps 1-4 + 6-7. Step 5 skipped. Complex findings assigned `fix_source: "manual"`.

**Why this works**: ARI rules have a `spec_mutation` flag. Rules that set it `True` can modify content. After mutation, ARI auto-rescans. Findings that survive all passes are the "complex" ones that get escalated to the LLM.

**AI escalation details**:
- **No cap on findings**: Every complex finding receives AI analysis - no `--max-ai-findings` limit
- **`--no-ai` flag**: Users can disable AI escalation entirely; complex findings are reported as-is without LLM analysis
- **Output patches**: Both a unified patch file (`rewrite-diff.patch`) and per-file patch files (`<filename>.patch`) are generated in the output directory. Patches contain **deterministic fixes only** (diffs of original → corrected output). AI suggestions are not included in patch files — they are delivered exclusively through the report output.

**ScanAgent structured output schema**:

The LLM response Pydantic model (`LLMRemediationResponse`) contains these fields only — `finding_id` is NOT part of this model:
```json
{
  "explanation": "Human-readable explanation of the issue and root cause",
  "suggested_code": "The replacement code snippet to apply",
  "confidence": 0.85,
  "reasoning": "Why this specific fix is recommended and any caveats",
  "applicable": true
}
```

The ScanAgent wrapper converts this to a full `AISuggestion` by injecting the `finding_id` from the Finding that triggered the escalation:
```json
{
  "finding_id": "f-012",
  "explanation": "...",
  "suggested_code": "...",
  "confidence": 0.85,
  "reasoning": "...",
  "applicable": true
}
```

- `confidence` (float, 0.0-1.0): LLM self-reported confidence. No separate validation step.
- `applicable` (bool): Whether the LLM believes it can generate a meaningful fix. `false` means the finding is too context-dependent for AI remediation (e.g., requires knowledge of infrastructure topology).
- The full `AISuggestion` entity (with `finding_id`) is defined in [data-model.md](data-model.md). The report formatter consumes `AISuggestion` objects — it never parses raw LLM output.
- The ScanAgent uses `invoke_structured()` (from `BaseAgent`) with the `LLMRemediationResponse` Pydantic model (without `finding_id`) to guarantee valid LLM output.

**Prompt template skeleton** (for `prompts/scanning/complex_remediation.md`):

The prompt sends each complex finding to the LLM with full context:

```markdown
You are an Ansible modernization expert. A static analysis tool (ARI) has flagged
the following issue in an Ansible playbook but cannot automatically fix it.

## Finding
- **Rule ID**: {rule_id}
- **Severity**: {severity}
- **File**: {file_path}:{line_number}
- **Description**: {description}
- **Target Ansible Version**: {target_version}

## Source Code Context
```yaml
{code_context}
```

## Task
Analyze this finding and provide a remediation suggestion. If you can generate
a concrete fix, provide the replacement code. If the issue requires knowledge
you don't have (e.g., infrastructure topology, runtime state), set applicable=false
and explain why in the reasoning field.

Respond with:
- explanation: What the issue is and why it matters
- suggested_code: The corrected YAML (or empty string if not applicable)
- confidence: Your confidence in the fix (0.0-1.0)
- reasoning: Why this fix is correct, or why a fix cannot be generated
- applicable: Whether you can provide a meaningful fix
```

The `{code_context}` includes 10 lines before and after the flagged line to give the LLM sufficient context for analysis. **Source of code context**: The code is read from the **original source directory**, NOT the output directory. This is intentional — deterministic fixes have already been applied to the output copy, so the AI sees the original unfixed code alongside the finding description. The finding's `file_path` (relative) is resolved against `ScanState.path` (the source directory) to read the original file. The `line_number` comes from the final ARI scan of the output directory, but since deterministic fixes for *other* rules may have shifted line numbers, the `ScanAgent` reads the original file and uses the line number as an approximate anchor, searching ±5 lines for the best match of the finding's context.

## R5: Version Auto-Detection Strategy

**Decision**: Infer source Ansible version from playbook patterns using heuristics.

**Detection signals**:
- Use of short-form module names (pre-2.10 pattern)
- Presence/absence of `collections:` keyword in playbooks (2.10+)
- Use of `include:` vs `include_tasks:`/`import_tasks:` (deprecated in 2.7)
- Module names that only existed in specific versions
- Presence of `ansible.cfg` with version-specific settings
- Collection `requirements.yml` format differences

**ARI already helps**: ARI's rules flag version-specific patterns. The detected patterns collectively indicate the approximate source version.

**Aggregation algorithm**:
1. Each detection signal maps to a version ceiling (e.g., short-form module names → ≤2.9, `include:` instead of `include_tasks:` → ≤2.7, no `collections:` keyword → ≤2.9)
2. Scan all playbook/role files and collect all triggered signals
3. The detected source version is the **minimum version ceiling** across all triggered signals (most conservative estimate)
4. If no version-specific signals are detected, default to `"unknown"` and log a warning. The scan proceeds assuming the most conservative source version (the oldest in `module_metadata.json`, currently 2.9), maximizing the set of detected version-specific issues.

**Conflicting signals**: In mixed-version repositories (e.g., one file uses `include:` suggesting ≤2.7, another uses `collections:` suggesting ≥2.10), the minimum-ceiling algorithm still takes the lowest value (2.7 in this example). This is intentionally conservative — the repository contains code that was written for an older version even if some files have been partially updated. The version detector logs a `WARNING` when conflicting signals are detected (signals whose ceilings span more than one major version), listing the conflicting signals and their source files, so the user is aware that the repository contains mixed-era code. The detected version is still the minimum, but the warning helps users understand why.

**Signal-to-version mapping** (initial set — extend as needed):

| Signal | Version Ceiling | Notes |
|--------|----------------|-------|
| Short-form module names (no FQCN) | 2.9 | FQCN required from 2.10 |
| `include:` instead of `include_tasks:` | 2.7 | `include:` deprecated in 2.7 |
| No `collections:` keyword in plays | 2.9 | `collections:` keyword added in 2.10 |
| `tower_*` module names | 2.13 | Renamed to `ansible.controller.*` in 2.14 |
| Bare `with_items` on complex data | 2.5 | Changed behavior in 2.5+ |

## R6: Output to Separate Directory

**Decision**: Write corrected files to `--output-dir` (default: `./modernized/`), preserving original directory structure.

**Implementation**:
1. Scan original files in-place (read-only)
2. Copy file tree to output directory
3. Apply ARI mutations to copies via `update_the_yaml_target()` on output files
4. Generate unified diff between original and output for each changed file
5. Write scan report (JSON/JUnit/text) to output directory

## R7: Custom Rules for Security, AAP, and EE Checks

**Decision**: Write custom ARI rules (Python dataclasses) for capabilities ARI doesn't cover natively.

**New rules needed**:

| Rule ID | Category | Severity | What It Checks |
|---------|----------|----------|----------------|
| SEC001 | Security | ERROR | Hardcoded passwords in task args |
| SEC002 | Security | ERROR | Hardcoded API keys/tokens |
| SEC003 | Security | ERROR | Private keys in plain text |
| AAP001 | AAP 2.x | ERROR | Legacy `tower_*` module usage |
| AAP002 | AAP 2.x | WARNING | Deprecated AWX callback plugins |
| AAP003 | AAP 2.x | WARNING | Legacy credential type references |
| EE001 | EE | WARNING | Undeclared collection dependencies |
| EE002 | EE | WARNING | System path and package assumptions |
| EE003 | EE | WARNING | Undeclared Python dependencies |

**Location**: `src/scanning/rules/` directory, loaded via ARI's `rules_dir` config.

**Rules directory concatenation**: The `Config.rules_dir` value MUST always include the APME built-in rules directory (`src/scanning/rules/`). When the user provides `--rules-dir`, the paths are concatenated with the built-in path:
```python
# In scan_service.py, when building Config:
apme_rules_dir = str(Path(__file__).parent / "rules")  # src/scanning/rules/
if user_rules_dir:
    rules_dir = f"{apme_rules_dir}:{user_rules_dir}"  # APME rules + user rules
else:
    rules_dir = apme_rules_dir  # APME rules only
```
This ensures SEC*, AAP*, and EE* rules are always active regardless of whether `--rules-dir` is provided.

**Rule-to-category mapping**:

| Rule ID | FindingCategory | Rationale |
|---------|----------------|-----------|
| P001 | VERSION | Module name validation is version-dependent |
| P002 | VERSION | Argument key changes are version-dependent |
| P003 | VERSION | Argument value constraints are version-dependent |
| P004 | VERSION | Variable validation relates to version behavior |
| R301 | FQCN | Non-FQCN use |
| R303 | QUALITY | Task naming is a best-practice concern |
| R116 | SECURITY | Insecure file permissions |
| SEC001 | SECURITY | Hardcoded passwords |
| SEC002 | SECURITY | Hardcoded API keys/tokens |
| SEC003 | SECURITY | Private keys in plain text |
| AAP001 | AAP | Legacy tower_* modules |
| AAP002 | AAP | Deprecated AWX callback plugins |
| AAP003 | AAP | Legacy credential type references |
| EE001 | EE | Undeclared collection dependencies |
| EE002 | EE | System path and package assumptions |
| EE003 | EE | Undeclared Python dependencies |

This mapping is implemented as a constant dict in `src/scanning/scan_service.py`. Unknown rule IDs from custom `--rules-dir` rules default to `VERSION`.

**ARI severity → APME Severity mapping**: ARI rules define severity as a lowercase string (e.g., `"error"`, `"warning"`, `"info"`). The `scan_service.py` module maps these to the APME `Severity` enum (`ERROR`, `WARNING`, `HINT`) using a constant dict: `{"error": Severity.ERROR, "warning": Severity.WARNING, "info": Severity.HINT, "none": Severity.HINT}`. ARI severity values not in this dict default to `Severity.HINT`. This mapping is applied when converting `RuleResult` objects to `Finding` entities.

**Custom ARI rule skeleton** (all SEC/AAP/EE rules follow this pattern):
```python
from dataclasses import dataclass
from ansible_risk_insight.models import (
    AnsibleRunContext,
    Rule,
    RuleResult,
    Severity,
    RuleTag as Tag,
)

@dataclass
class SEC001HardcodedPassword(Rule):
    rule_id: str = "SEC001"
    description: str = "Hardcoded passwords in task args"
    severity: str = "error"
    enabled: bool = True
    tags: tuple = ("security",)

    def match(self, ctx: AnsibleRunContext) -> bool:
        """Return True if this rule applies to the given context node.
        `ctx` provides: ctx.current (RunTarget), ctx.sequence (RunTargetList),
        ctx.parent (Object), ctx.root_key (str), ctx.vars, ctx.host_info.
        Access task arguments via ctx.current's spec/resolved attributes."""
        current = ctx.current
        return current is not None and hasattr(current, 'spec') and current.spec is not None

    def process(self, ctx: AnsibleRunContext) -> RuleResult:
        """Analyze the matched node and return a RuleResult.
        Set verdict=True if the rule is violated, False if clean.
        Note: detail is a dict, not a string."""
        import re
        pattern = r'(?i)(password|passwd|pass)\s*[:=]\s*[\'"][^\'"{$]+[\'"]'
        module_args = self._extract_module_args(ctx.current)
        if module_args:
            for key, value in module_args.items():
                if isinstance(value, str) and re.search(pattern, f"{key}: '{value}'"):
                    if not self._is_vault_or_jinja(value):
                        return RuleResult(
                            verdict=True,
                            detail={"message": f"Hardcoded password detected in '{key}'"},
                        )
        return RuleResult(verdict=False)

    def _extract_module_args(self, current) -> dict | None:
        """Extract module arguments from the current RunTarget.
        Implementation must inspect current.spec — see ARI's built-in
        rules (e.g., P001_module_name_validation.py) for the exact
        attribute access pattern for module_options/resolved_args."""
        if hasattr(current, 'spec') and hasattr(current.spec, 'module_options'):
            return current.spec.module_options
        return None

    def _is_vault_or_jinja(self, value: str) -> bool:
        return any(marker in value for marker in ["!vault", "$ANSIBLE_VAULT;", "{{"])
```

**Important ARI API notes for custom rule authors**:
- The base class is `Rule` (from `ansible_risk_insight.models`), **NOT** `AnsibleRiskRule`
- `Rule` inherits from `RuleMetadata` which provides `rule_id`, `description`, `severity`, `name`, `version`, `tags`
- `Rule` adds `enabled` (bool, default `False` — **set to `True` for custom rules**), `precedence` (int), `spec_mutation` (bool)
- `RuleResult.detail` is a **dict**, not a string — use `{"message": "..."}` format
- The context parameter is `AnsibleRunContext`, which has `current` (RunTarget), `sequence` (RunTargetList), `parent` (Object), `root_key` (str), `vars`, `host_info` — NOT `ctx.module_args` or `ctx.file_path` directly
- Consult ARI's built-in rules (e.g., `P001_module_name_validation.py`) for the exact pattern of extracting module names and arguments from `ctx.current`


**`spec_mutation` and custom rules**: Most custom rules do **not** set `spec_mutation` on the dataclass (the attribute defaults to `False` via `getattr` in `is_finding_resolvable()`). Findings from rules without `spec_mutation` are classified as complex and escalated to AI (or reported as `fix_source: "manual"` with `--no-ai`).

Rules with `spec_mutation=True` (deterministic auto-fixing):
- ARI built-in: R301, P001, P002, P003, P004
- Custom: **AAP001** (simple module redirect: `tower_job_launch` → `ansible.controller.job_launch`, implemented via the same spec_mutation mechanism as R301 FQCN renames)

Rules without `spec_mutation` (detection-only → AI escalation):
- SEC001, SEC002, SEC003 (secret detection — no automated fix)
- AAP002, AAP003 (deprecated callback plugins, credential types — require context-dependent migration)
- EE001, EE002, EE003 (dependency/path validation — no code mutation)

**Secret detection approach**: Custom regex-based ARI rules (no external library dependency).

**Exact regex patterns per rule**:

| Rule | Pattern | What It Matches |
|------|---------|-----------------|
| SEC001 | `(?i)(password\|passwd\|pass)\s*[:=]\s*['"][^'"{$]+['"]` | Password fields with literal string values (excludes Jinja2 `{{ }}` and vault references) |
| SEC002 | `(AKIA[0-9A-Z]{16})\|((?i)(api_key\|apikey\|api_token\|access_token\|auth_token\|secret_key)\s*[:=]\s*['"][^'"{$]+['"])` | AWS access key IDs (`AKIA...`) and common API key/token variable names with literal values |
| SEC003 | `-----BEGIN\s+(RSA\|DSA\|EC\|OPENSSH\|PGP)?\s*PRIVATE KEY-----` | Private key headers in PEM format |

**Vault exclusion logic**: All SEC rules skip values containing `!vault`, `$ANSIBLE_VAULT;`, or Jinja2 expressions (`{{ }}`). This prevents false positives on properly secured values.

## R8: Module Version Metadata Source

**Decision**: Build the module change metadata programmatically from `ansible-doc` output and Ansible changelogs.

**Rationale**: This provides the most accurate and comprehensive mapping. Each Ansible version's `ansible-doc --list` output and porting guides contain the authoritative record of module renames, parameter changes, and deprecations.

**Approach**:
1. For each major Ansible version (2.9, 2.10, 2.11, ..., current), extract the module list via `ansible-doc --list --json`
2. Parse Ansible's official porting guides (e.g., `porting_guide_2.10.rst`) for explicit module renames and deprecations
3. Diff module lists between versions to detect additions, removals, and renames
4. Store as a versioned JSON mapping file in `src/scanning/data/module_metadata.json`
5. This can be regenerated/updated when new Ansible versions release

**Storage**: Static committed artifact at `src/scanning/data/module_metadata.json`. This file is checked into the repository and manually refreshed when new Ansible versions release. No runtime network calls or dynamic generation.

**Bootstrap process**:

A generation script at `scripts/generate_module_metadata.py` automates the creation and refresh of this file:

1. **Prerequisites**: Requires `ansible-core` installed (the script invokes `ansible-doc`). A Python virtualenv with the target Ansible version is recommended.
2. **Execution**: `uv run scripts/generate_module_metadata.py --ansible-versions 2.9,2.10,2.11,2.12,2.13,2.14,2.15,2.16,2.17 --output src/scanning/data/module_metadata.json`
3. **What it does**:
   - For each specified version, runs `ansible-doc --list --json` to extract the full module catalog
   - Parses Ansible porting guide RST files (bundled in `ansible-core` at `docs/docsite/rst/porting_guides/`) for explicit deprecation and rename entries
   - Diffs module lists between adjacent versions to compute `version_diffs`
   - Merges porting guide data (renames, parameter changes) into the module entries
   - Writes the final JSON to `--output`
4. **When to run**: Once during initial development, then whenever a new Ansible version is released. The generated file is committed to the repo — CI does NOT run this script.
5. **A pre-built version ships with the repo**: The initial `module_metadata.json` covering Ansible 2.9 through 2.17 will be generated and committed as part of the first implementation task. Developers do not need to run the script unless updating for a new Ansible release.

**`latest` version resolution**: When `--target-version` is omitted (or explicitly set to `"latest"`), the tool resolves "latest" to the highest version key present in the `version_diffs` section of `module_metadata.json`. Specifically, it parses all `X.Y_to_X.Z` keys and takes the maximum `X.Z` value. For the shipped metadata covering Ansible 2.9–2.17, this resolves to `"2.17"`. When the metadata file is regenerated for a new Ansible release, the "latest" resolution automatically picks up the new highest version with no code changes required.

**JSON schema** (defined in [data-model.md](data-model.md)):
```json
{
  "schema_version": "1.0",
  "generated_from": "ansible-core 2.17.1",
  "generated_at": "2026-03-04T00:00:00Z",
  "modules": {
    "apt": {
      "fqcn": "ansible.builtin.apt",
      "short_name": "apt",
      "introduced": "0.0.2",
      "deprecated": null,
      "removed": null,
      "redirect_from": [],
      "parameters": {
        "use": {
          "deprecated_in": "2.14",
          "removed_in": null,
          "renamed_to": "update_cache",
          "notes": "Parameter 'use' renamed for clarity"
        }
      }
    },
    "tower_job_launch": {
      "fqcn": "awx.awx.tower_job_launch",
      "short_name": "tower_job_launch",
      "introduced": "2.3",
      "deprecated": "2.14",
      "removed": null,
      "redirect_from": [],
      "replacement": "ansible.controller.job_launch",
      "parameters": {}
    }
  },
  "version_diffs": {
    "2.9_to_2.10": {
      "modules_added": ["ansible.builtin.apt", "..."],
      "modules_removed": [],
      "modules_renamed": {"apt": "ansible.builtin.apt"},
      "parameters_changed": {}
    }
  }
}
```
- `modules`: Keyed by short name. Each entry has FQCN, lifecycle versions, parameter changes, and replacement pointers.
- `redirect_from`: Lists other module names that redirect TO this entry. For most modules this is empty. Example: if `ansible.netcommon.net_ping` was later consolidated into `ansible.builtin.ping`, then the `ping` entry would have `redirect_from: ["net_ping"]`. The short-name → FQCN mapping (e.g., `apt` → `ansible.builtin.apt`) is captured in `version_diffs.modules_renamed`, not `redirect_from`.
- `version_diffs`: Pre-computed diffs between adjacent versions. The scanner uses these to report "changed in version X" context.
- `parameters`: Only tracks parameters that changed (deprecated/renamed/removed). Stable parameters are not listed.

**Alternatives Considered**:
- ARI's built-in knowledge only: Insufficient for the full 2.9-to-current mapping the PRD requires
- Manual curation: Not scalable and error-prone
- Ansible Galaxy API: Doesn't provide version-to-version diff data
- Runtime generation: Adds startup latency, requires `ansible-doc` on the host, and makes builds non-deterministic

## R9: Test Data Strategy

**Decision**: Create synthetic test fixtures with known issues for controlled, reproducible testing.

**Fixture categories**:

| Directory | Purpose | Contains |
|-----------|---------|----------|
| `tests/scanning/fixtures/legacy_29/` | Ansible 2.9 era patterns | Short-form modules, `include:`, old syntax |
| `tests/scanning/fixtures/legacy_212/` | Ansible 2.12 era patterns | Mixed FQCN, deprecated params |
| `tests/scanning/fixtures/secrets/` | Hardcoded secrets | Passwords, API keys, private keys, vault-encrypted (clean) |
| `tests/scanning/fixtures/awx_legacy/` | AWX/Tower patterns | `tower_*` modules, old callback plugins |
| `tests/scanning/fixtures/ee_issues/` | EE incompatibilities | Missing requirements.yml, system path assumptions |
| `tests/scanning/fixtures/complex/` | AI escalation triggers | Dynamic module names via Jinja2, complex conditionals, custom filter plugins, multi-file variable resolution |
| `tests/scanning/fixtures/clean/` | Fully modern playbooks | Should produce zero findings |

Each fixture playbook has a corresponding expected-results file documenting exactly which findings should be detected, enabling automated assertion testing.

## R10: Execution Environment Allowlist

**Decision**: Maintain a static JSON allowlist at `src/scanning/data/ee_baseline.json` containing known-safe paths, system packages, and Python packages available in the `ee-supported-rhel9` base image.

**Allowlist format**:
```json
{
  "schema_version": "1.0",
  "base_image": "ee-supported-rhel9",
  "generated_at": "2026-03-04T00:00:00Z",
  "system_paths": {
    "writable": ["/tmp", "/var/tmp", "/home/runner"],
    "readable": ["/etc/ansible", "/usr/share/ansible", "/etc/pki"]
  },
  "system_packages": [
    "python3", "git", "rsync", "openssh-clients", "sshpass",
    "podman", "buildah", "skopeo", "unzip", "bzip2", "tar"
  ],
  "python_packages": [
    "ansible-core", "ansible-runner", "pyyaml", "jinja2",
    "cryptography", "paramiko", "requests", "urllib3",
    "jmespath", "xmltodict", "netaddr"
  ],
  "collections_bundled": [
    "ansible.builtin", "ansible.posix", "ansible.utils",
    "ansible.netcommon", "ansible.windows"
  ]
}
```

**How it's generated**: Derived from the `ee-supported-rhel9` container image manifest and `pip list` / `rpm -qa` output. A helper script `scripts/generate_ee_baseline.py` automates this by running `podman inspect` and `podman run` commands against the image. Like `module_metadata.json`, this is committed to the repo and refreshed when Red Hat publishes a new EE base image.

**How `--ee-config` overrides work**: When `--ee-config execution-environment.yml` is passed:
1. The EE definition file is parsed (format matches `ansible-builder` EE v3 schema)
2. `additional_system_packages` from the EE definition are merged into `system_packages`
3. Python packages from `requirements.txt` referenced by the EE definition are merged into `python_packages`
4. Collections from `requirements.yml` referenced by the EE definition are merged into `collections_bundled`
5. The expanded allowlist is then used for EE001/EE002/EE003 rule checks

**EE v3 schema keys used** (subset of `ansible-builder` EE definition v3 — see [ansible-builder docs](https://ansible.readthedocs.io/projects/builder/en/stable/definition/) for full schema):
```yaml
version: 3                              # Must be 3
dependencies:
  galaxy: requirements.yml              # Path to collection requirements (string)
  python: requirements.txt              # Path to Python requirements (string)
  system:                               # Inline list of system packages
    - gcc
    - libffi-devel
additional_build_steps: ...             # Ignored by APME
images: ...                             # Ignored by APME
```
APME reads only `dependencies.galaxy` (path string → parsed as `requirements.yml`), `dependencies.python` (path string → read as pip requirements), and `dependencies.system` (list of package names). All other keys are ignored. Paths are resolved relative to the EE definition file's directory.

If no `--ee-config` is provided, the default `ee_baseline.json` is used as-is.

**Rule-to-allowlist mapping**:
- **EE001** (undeclared collections): Checks if used collections appear in `collections_bundled` OR in the project's `requirements.yml`. Missing from both = finding.
- **EE002** (system path and package assumptions): Checks two classes of system-level assumptions: (1) filesystem paths — referenced paths are checked against `system_paths.writable` and `system_paths.readable`, unknown paths = finding; (2) system package references — tasks invoking package manager modules (`ansible.builtin.package`, `ansible.builtin.yum`, `ansible.builtin.apt`, `ansible.builtin.dnf`) or `ansible.builtin.command`/`ansible.builtin.shell` with commands that reference system executables are checked against the `system_packages` allowlist, unknown packages = finding. This covers FR-021's requirement for "filesystem paths, system packages, writable system locations."
- **EE003** (undeclared Python deps): Checks if imported packages appear in `python_packages` OR in the project's `requirements.txt`. Missing from both = finding.