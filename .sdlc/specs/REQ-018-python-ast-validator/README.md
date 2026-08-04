# REQ-018: Python AST Validator for Plugin Deprecations

**Status:** Draft
**Created:** 2026-07-13
**Priority:** Low
**Phase:** PHASE-002
**Source:** Ansible 2.23 DWCODE deprecation warnings

## Summary

Add a new Python AST validator service to detect deprecated method calls, attribute access, and constructor arguments in Ansible plugin code. This enables detection of deprecations that cannot be found via simple regex/import scanning.

## Background

REQ-017 covers deprecated imports (regex-detectable). This REQ addresses deprecations requiring semantic analysis:

- Method calls on specific classes
- Attribute access patterns
- Constructor argument usage
- Subclass method implementations

These require parsing Python into an AST and analyzing call sites, not just import statements.

## Deprecations Covered

### Callback Plugin Methods

| Rule | DWCODE | Pattern | Description |
|------|--------|---------|-------------|
| M035 | `callback_v2_on_any` | `def v2_on_any(self, ...)` | Deprecated callback method |
| M036 | `callback_v1_methods` | `def playbook_on_*`, `def runner_on_*` | V1 callback methods |

### Shell/PowerShell Plugin Methods

| Rule | DWCODE | Pattern | Description |
|------|--------|---------|-------------|
| M037 | `shell_wrap_for_exec` | `self.wrap_for_exec(...)` | Deprecated Shell method call |
| M038 | `shell_checksum` | `self.checksum(...)` | Deprecated ShellModule method |
| M039 | `powershell_encode_script` | `self._encode_script(...)` | Deprecated PowerShell method |

### Inventory Plugin Arguments

| Rule | DWCODE | Pattern | Description |
|------|--------|---------|-------------|
| M040 | `constructable_disable_lookups` | `disable_lookups` arg usage | No-op argument |

### PluginLoader Deprecations

| Rule | DWCODE | Pattern | Description |
|------|--------|---------|-------------|
| M041 | `pluginloader_aliases_arg` | `PluginLoader(..., aliases=...)` | Deprecated constructor arg |
| M042 | `pluginloader_aliases_property` | `.aliases` attribute access | Deprecated property |

### Templar Internal Access

| Rule | DWCODE | Pattern | Description |
|------|--------|---------|-------------|
| M043 | `templar_available_variables` | `._available_variables` access | Internal attribute |
| M044 | `templar_loader` | `._loader` access | Internal attribute |
| M045 | `templar_environment` | `.environment` access on Templar | Deprecated attribute |
| M046 | `templar_copy_context_overrides` | `copy_with_new_env(...)` with overrides | Deprecated arguments |

## Requirements

### Functional

1. **PythonASTValidator Service**: New gRPC validator on port 50062 (50058 is Collection Health; 50059 Dep Audit; 50060 Gateway gRPC)
2. **AST Parsing**: Parse Python files using `ast` module
3. **Pattern Detection**:
   - Method definitions (`def method_name`)
   - Method calls (`obj.method()`)
   - Attribute access (`obj.attr`)
   - Constructor arguments (`Class(..., arg=)`)
4. **File Scope**: Scan `*.py` in plugin directories
5. **Context Awareness**: Track class inheritance where feasible
6. **Line/Column Numbers**: Precise source locations

### Non-Functional

1. **Severity**: HIGH (breaking changes)
2. **Tags**: `[modernization, python, deprecated]`
3. **Performance**: Cache parsed ASTs per scan
4. **Isolation**: Separate container like other validators

## Architecture

### New Service: `apme-python-validator`

```text
┌──────────────────────────────────────────────────┐
│  Primary Orchestrator :50051                     │
│    ├── Native :50055 (YAML rules)                │
│    ├── OPA :50054 (Rego rules)                   │
│    ├── Ansible :50053 (runtime checks)           │
│    ├── Gitleaks :50056 (secrets)                 │
│    └── Python :50062 (AST rules) ← NEW           │
└──────────────────────────────────────────────────┘
```

### Proto Definition

Implements the shared `Validator` service from `proto/apme/v1/validate.proto` (ADR-001). No separate proto file — Primary fans out via the same `Validate(ValidateRequest) returns (ValidateResponse)` contract used by Native, OPA, Ansible, and Gitleaks.

Python file content is delivered through `ValidateRequest.files` (path + raw bytes). Rule enablement follows the standard validator registration path. No adapter layer required.

### AST Visitor Pattern

Visitors scope matches to plugin file context (directory path) and enclosing class hierarchy to avoid flagging unrelated helpers:

```python
class DeprecationVisitor(ast.NodeVisitor):
    """Detect deprecated patterns in Python AST."""

    # File-scope gates (set before visiting each file)
    CALLBACK_DIRS = ("callback_plugins", "plugins/callback")
    SHELL_DIRS = ("shell_plugins", "plugins/shell")
    TEMPLAR_CONTEXT = "templar"  # class name or module path hint

    def visit_FunctionDef(self, node: ast.FunctionDef):
        # M035/M036: callback methods — only in callback plugin files
        if self.file_scope == "callback":
            if node.name == "v2_on_any":
                self.report("M035", node, "v2_on_any callback method is deprecated")
            elif node.name.startswith(("playbook_on_", "runner_on_")):
                self.report("M036", node, f"V1 callback method {node.name} is deprecated")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute):
        # M043: Templar._available_variables — only when enclosing class is Templar
        if node.attr == "_available_variables" and self.enclosing_class == "Templar":
            self.report("M043", node, "Direct access to _available_variables is deprecated")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call):
        # M037: Shell.wrap_for_exec — only in shell plugin files
        if (
            self.file_scope == "shell"
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "wrap_for_exec"
        ):
            self.report("M037", node, "Shell.wrap_for_exec method is deprecated")
        self.generic_visit(node)
```

`file_scope` is derived from the file path (`callback_plugins/`, `shell_plugins/`, etc.). `enclosing_class` is tracked via a class stack during `visit_ClassDef`. Acceptance tests must confirm unrelated functions and objects in non-plugin files are not reported.

## Acceptance Criteria

- [ ] PythonASTValidator service starts on :50062
- [ ] Primary orchestrator fans out to Python validator
- [ ] M035 detects `v2_on_any` method definition
- [ ] M036 detects v1 callback method patterns
- [ ] M037-M039 detect deprecated shell method calls
- [ ] M040-M042 detect PluginLoader deprecations
- [ ] M043-M046 detect Templar internal access
- [ ] Scoped matching: callback rules ignore non-callback files; shell rules ignore non-shell files; Templar rules require enclosing Templar class
- [ ] Container builds and runs in pod
- [ ] Proto definitions and codegen complete
- [ ] `tox -e lint` passes
- [ ] `tox -e unit` passes with coverage
- [ ] `tox -e integration` includes Python validator tests

## Technical Notes

### Limitations

- **No cross-file analysis**: Can't track imports across modules
- **No type inference**: Can't determine if `obj.checksum()` is on ShellModule vs other class
- **Heuristics**: Rely on method/attribute names being distinctive enough

### Mitigation

- Callback plugins: file location (`callback_plugins/`) implies context
- Shell plugins: file location (`shell_plugins/`) implies context
- High-confidence patterns: `_available_variables`, `wrap_for_exec` are distinctive

### Alternative: Use Existing Python Tooling

Could delegate to `pylint` or `pyright` with custom plugins, but:
- Adds external dependency
- Less control over output format
- Harder to integrate with gRPC pipeline

Prefer native `ast` module for simplicity and control.

## Dependencies

- ADR for new validator service (ADR-055 or similar)
- Container image build integration
- Primary orchestrator fan-out extension

## Out of Scope

- Full type checking (use pyright/mypy separately)
- Security scanning of Python (use Bandit separately)
- Runtime behavior analysis

## References

- REQ-017 — Python import deprecation detection (regex, quick wins)
- ADR-001 — gRPC communication
- ADR-007 — Async gRPC servers
- ADR-008 — Rule ID conventions
- Ansible deprecation source: `lib/ansible/utils/display.py`
