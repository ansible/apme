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

### AST Rule Predicates (M035-M046)

Each rule below defines an executable predicate (no type inference). `file_scope` is derived from the file path; `enclosing_class` is tracked via a class stack during `visit_ClassDef`. Predicates return a violation or nothing.

| Rule | File scope | AST predicate | Positive example | Negative example |
|------|-----------|---------------|------------------|------------------|
| M035 | `callback_plugins/` or `plugins/callback/` | `visit_FunctionDef`: `node.name == "v2_on_any"` | `def v2_on_any(self, result):` in callback plugin | Same method name in `module_utils/helper.py` |
| M036 | callback scope | `visit_FunctionDef`: `node.name.startswith(("playbook_on_", "runner_on_"))` | `def playbook_on_start(self):` | `def playbook_on_start(self):` in a test helper |
| M037 | `shell_plugins/` or `plugins/shell/` | `visit_Call`: `isinstance(node.func, ast.Attribute) and node.func.attr == "wrap_for_exec"` | `self.wrap_for_exec(cmd)` in shell plugin | `self.wrap_for_exec(cmd)` in action plugin |
| M038 | shell scope | `visit_Call`: `node.func.attr == "checksum"` and receiver is `self` | `self.checksum(path)` in shell plugin | `obj.checksum(path)` in unrelated module |
| M039 | shell scope (powershell file) | `visit_Call`: `node.func.attr == "_encode_script"` | `self._encode_script(script)` in `powershell.py` | Same call in non-shell file |
| M040 | `inventory_plugins/` or `plugins/inventory/` | `visit_Call`: any `keyword.arg == "disable_lookups"` | `Constructable(..., disable_lookups=True)` | `templar.template(disable_lookups=True)` (Templar API, out of scope for M040) |
| M041 | any plugin file importing `PluginLoader` | `visit_Call`: callee name is `PluginLoader` and any `kw.arg == "aliases"` | `PluginLoader("cache", aliases=["json"])` | `SomeClass(aliases=["json"])` |
| M042 | same as M041 | `visit_Attribute`: `node.attr == "aliases"` and receiver variable assigned from `PluginLoader(...)` in same function | `loader.aliases` after `loader = PluginLoader(...)` | `config.aliases` on unrelated object |
| M043 | any file | `visit_Attribute`: `node.attr == "_available_variables"` and `enclosing_class == "Templar"` | `self._available_variables` inside `class Templar` | `self._available_variables` in `class Helper` |
| M044 | any file | `visit_Attribute`: `node.attr == "_loader"` and `enclosing_class == "Templar"` | `self._loader` in Templar subclass | `self._loader` elsewhere |
| M045 | any file | `visit_Attribute`: `node.attr == "environment"` and `enclosing_class == "Templar"` | `self.environment` in Templar class | `self.environment` in config parser |
| M046 | any file | `visit_Call`: `node.func.attr == "copy_with_new_env"` and (`environment_class` keyword present OR any other keyword arg) | `self.copy_with_new_env(environment_class=Env)` | `self.copy_with_new_env()` with no kwargs |

M042 assignment tracking: on `visit_Assign`, record `{target.id: "PluginLoader"}` when the RHS is a `Call` with callee `PluginLoader`. Clear on function exit.

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
    INVENTORY_DIRS = ("inventory_plugins", "plugins/inventory")

    def visit_FunctionDef(self, node: ast.FunctionDef):
        # M035/M036: callback methods — only in callback plugin files
        if self.file_scope == "callback":
            if node.name == "v2_on_any":
                self.report("M035", node, "v2_on_any callback method is deprecated")
            elif node.name.startswith(("playbook_on_", "runner_on_")):
                self.report("M036", node, f"V1 callback method {node.name} is deprecated")
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign):
        # M042: track PluginLoader assignments for .aliases detection
        if isinstance(node.value, ast.Call) and self._callee_name(node.value) == "PluginLoader":
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.pluginloader_vars.add(target.id)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute):
        # M042: PluginLoader.aliases property
        if node.attr == "aliases" and isinstance(node.value, ast.Name):
            if node.value.id in self.pluginloader_vars:
                self.report("M042", node, "PluginLoader.aliases property is deprecated")
        # M043-M045: Templar internal attributes
        if self.enclosing_class == "Templar":
            if node.attr == "_available_variables":
                self.report("M043", node, "Direct access to _available_variables is deprecated")
            elif node.attr == "_loader":
                self.report("M044", node, "Direct access to _loader is deprecated")
            elif node.attr == "environment":
                self.report("M045", node, ".environment on Templar is deprecated")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call):
        # M037-M039: shell plugin method calls
        if self.file_scope == "shell" and isinstance(node.func, ast.Attribute):
            if node.func.attr == "wrap_for_exec":
                self.report("M037", node, "Shell.wrap_for_exec method is deprecated")
            elif node.func.attr == "checksum":
                self.report("M038", node, "ShellModule.checksum method is deprecated")
            elif node.func.attr == "_encode_script":
                self.report("M039", node, "PowerShell._encode_script method is deprecated")
        # M040: disable_lookups in inventory plugins
        if self.file_scope == "inventory":
            for kw in node.keywords:
                if kw.arg == "disable_lookups":
                    self.report("M040", node, "disable_lookups argument is deprecated")
        # M041: PluginLoader(aliases=...)
        if self._callee_name(node) == "PluginLoader":
            for kw in node.keywords:
                if kw.arg == "aliases":
                    self.report("M041", node, "PluginLoader aliases= argument is deprecated")
        # M046: copy_with_new_env with deprecated overrides
        if isinstance(node.func, ast.Attribute) and node.func.attr == "copy_with_new_env":
            deprecated_kwargs = [kw.arg for kw in node.keywords if kw.arg]
            if deprecated_kwargs:
                self.report("M046", node, f"copy_with_new_env kwargs {deprecated_kwargs} are deprecated")
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

- [ ] Integration test sends `ValidateRequest.files` through Primary to Python AST validator and asserts M035 violation

## Service Wiring

Follow the same pattern as Collection Health (`:50058`) and Dep Audit (`:50059`). Implementation artifacts:

| Artifact | Value |
|----------|-------|
| `VALIDATOR_ENV_VARS` key | `"python_ast": "PYTHON_AST_GRPC_ADDRESS"` |
| Primary env var | `PYTHON_AST_GRPC_ADDRESS=127.0.0.1:50062` |
| Listen env var | `APME_PYTHON_AST_VALIDATOR_LISTEN=0.0.0.0:50062` |
| CLI entry point | `apme-python-ast-validator = apme_engine.daemon.python_ast_validator_main:main` |
| Server module | `src/apme_engine/daemon/python_ast_validator_server.py` |
| Main module | `src/apme_engine/daemon/python_ast_validator_main.py` |
| Container image | `containers/python-ast/Dockerfile` → `apme-python-ast:latest` |
| Pod container name | `python-ast` |
| OTEL service name | `apme-python-ast` |

### Files to modify (implementation task)

1. `src/apme_engine/daemon/primary_server.py` — add `"python_ast": "PYTHON_AST_GRPC_ADDRESS"` to `VALIDATOR_ENV_VARS`
2. `src/apme_engine/daemon/launcher.py` — add port `50062`, env var mapping, and `serve()` call
3. `pyproject.toml` — add `apme-python-ast-validator` console script
4. `containers/podman/pod.yaml` — add `python-ast` container; set `PYTHON_AST_GRPC_ADDRESS` on Primary
5. `deploy/helm/apme/templates/engine-deployment.yaml` — add env vars (mirror dep-audit pattern)
6. `src/apme_gateway/api/router.py` — add health-check entry for Python AST validator

### Health and failure behavior

- Implements `Validator.Health` returning `HealthResponse` with rule count (same contract as Native/Gitleaks)
- Primary fan-out uses `asyncio.gather(..., return_exceptions=True)` — validator failure yields empty violations + diagnostic error, scan continues
- Readiness: container starts when gRPC server binds `:50062`; Primary skips validator when `PYTHON_AST_GRPC_ADDRESS` is unset (optional service, like Gitleaks)

### Integration test

Add `tests/integration/test_python_ast_validator.py`:

```python
@pytest.mark.integration
async def test_primary_fans_out_python_files_to_ast_validator(daemon_env):
    """ValidateRequest.files with deprecated callback reaches Python AST validator."""
    # 1. Ensure PYTHON_AST_GRPC_ADDRESS is set in daemon fixture
    # 2. Submit a .py file under plugins/callback/ containing def v2_on_any
    # 3. Assert response includes M035 violation with line number
```

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

- ADR-055 (new validator service) — documents port assignment and optional-service classification
- Implementation task(s) derived from this spec cover the artifacts listed in Service Wiring above

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
