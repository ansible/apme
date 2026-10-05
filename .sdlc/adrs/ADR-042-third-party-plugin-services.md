# ADR-042: Third-Party Plugin Services

## Status

Partially Implemented (operator `spec.plugins[]`, PyPI SDK, per-plugin AI batching pending)

## Date

2026-03-20

## Context

APME's built-in validators (Native, OPA, Ansible, Gitleaks) cover a broad set of lint, modernization, risk, and secrets checks. However, organizations need to enforce their own policies — coding standards, compliance gates, security baselines — that are specific to their environment and out of scope for the built-in rule set.

The question is: **where should extensibility live?**

Three layers were considered:

1. **Rule layer** — load custom rules into existing validators (Python classes into Native, Rego files into OPA)
2. **Transform layer** — register custom transforms in the built-in `TransformRegistry`
3. **Service layer** — let third parties bring their own container that validates *and* transforms

Key constraints:

- ADR-009 establishes that validators are read-only; remediation is separate. Extending a built-in validator with transform capability would violate this.
- Rule-layer extensibility locks third parties into a specific language (Python for Native, Rego for OPA) and mixes untrusted code into core services.
- Third-party checks and their fixes share domain knowledge — splitting them across two separate extension points (one for detection, one for fixing) creates unnecessary friction.
- The gRPC architecture already provides a natural service boundary: any container implementing the right RPCs can participate in the fan-out.

## Decision

**We will provide extensibility at the service layer through a `Plugin` gRPC service that combines validation and transformation in a single contract.**

Third parties build a container that implements the `Plugin` service. The Engine discovers plugin containers via environment variables, fans out `Validate` calls alongside built-in validators, and routes `Transform` calls back to the originating plugin during fix passes. A Python SDK (`apme-plugin-sdk`) lowers the authoring barrier to ~20 lines of code.

### 1. Plugin gRPC service

A new service definition in `proto/apme/v1/plugin.proto`:

```protobuf
syntax = "proto3";

package apme.v1;

import "apme/v1/common.proto";
import "apme/v1/validate.proto";

service Plugin {
  rpc Validate(ValidateRequest) returns (ValidateResponse);
  rpc Transform(TransformRequest) returns (TransformResponse);
  rpc Describe(DescribeRequest) returns (DescribeResponse);
  rpc Health(HealthRequest) returns (HealthResponse);
}

message TransformRequest {
  string request_id = 1;
  File file = 2;
  Violation violation = 3;
  bytes hierarchy_payload = 4;
}

message TransformResponse {
  string request_id = 1;
  File file = 2;
  bool applied = 3;
  string error = 4;
}

message DescribeRequest {}

message DescribeResponse {
  string name = 1;
  string version = 2;
  string rule_id_prefix = 3;
  repeated string transform_rule_ids = 4;
  map<string, string> metadata = 5;
}
```

- **Validate** reuses existing `ValidateRequest`/`ValidateResponse`. The Engine sends `request_id`, `files`, `hierarchy_payload` (JSON), `ansible_core_version`, and `collection_specs`. It does not forward `scandata`, `venv_path`, or `content_graph_data`.
- **Transform** receives one file and the violation to fix, plus hierarchy context. Returns the transformed file or `applied=false` / an error.
- **Describe** is used for version, optional prefix, and `transform_rule_ids`. Plugin **identity** is the env-var token (`APME_PLUGIN_<NAME>_ADDRESS` → name `<name>`), never `Describe.name` (a sidecar must not impersonate `opa` / `native`). The Engine keeps only Transform IDs under `EXT-<name>-`.
- **Health** reuses the existing `HealthRequest`/`HealthResponse` from `common.proto`. Plugin Health is probed with the required validators but is never `required` for Engine aggregate status.

### 2. Rule ID convention: EXT- prefix

Extends ADR-008. All plugin-produced rule IDs must use the `EXT-` prefix:

```
EXT-<plugin_name>-<NNN>
```

Examples: `EXT-secteam-001`, `EXT-orgpolicy-003`, `EXT-compcheck-010`.

- The Engine enforces the prefix: violations from plugin services that do not start with `EXT-` are rejected or logged and dropped.
- The `EXT-` prefix is how the remediation path distinguishes plugin-owned violations from built-in ones.
- The SDK auto-prefixes rule IDs — plugin authors specify only the numeric suffix (e.g., `"001"`), and the SDK prepends `EXT-<name>-`.

### 3. Discovery via environment variables

Consistent with ADR-005 (fixed-port env vars, no service discovery):

```
APME_PLUGIN_<NAME>_ADDRESS=host:port
```

Examples:

```bash
APME_PLUGIN_SECTEAM_ADDRESS=localhost:50100
APME_PLUGIN_ORGPOLICY_ADDRESS=localhost:50101
```

- The Engine scans env vars matching `APME_PLUGIN_*_ADDRESS` at startup.
- For each discovered plugin, it calls `Describe` to obtain the rule ID prefix and transform capabilities.
- If `Describe` is unimplemented (returns `UNIMPLEMENTED`), the Engine infers the plugin name from the env var and uses `EXT-<name_lowercase>-` as the prefix.
- Plugins are called in parallel alongside built-in validators during `_scan_pipeline`.

### 4. Remediation routing

During fix passes, the Engine (or Remediation Engine) routes violations by rule ID prefix:

- **Built-in prefixes** (L, M, R, P, SEC) — routed to the built-in `TransformRegistry` as today.
- **EXT- prefix** — routed to the originating plugin's `Transform` RPC. The Engine maintains a `rule_prefix -> plugin_address` map built from `Describe` responses.

Plugin transforms participate in the same convergence loop: scan -> fix -> rescan -> repeat until stable.

**Graph-safe Transform (amendment, ADR-044):** Built-in Tier 1 uses node-local `CommentedMap` functions via `TransformRegistry` / `ContentGraph.apply_transform`. Plugins do **not** receive `TransformSession` over gRPC (that API is engine-internal). Instead:

- `TransformRequest.file.content` is the **current node YAML fragment** (`ContentNode.yaml_lines`); `file.path` is the playbook path.
- The plugin returns replacement node YAML (`applied=true`) or `applied=false`.
- The Engine checks YAML well-formedness, then `ContentGraph.apply_yaml()` and marks the node dirty. Plugins never write `/sessions` or the user tree.

Whole-file rewrites (e.g. wrapping a SAST tool that emits unified diffs) remain a future extension; v1 is node-scoped so identity and convergence stay intact.

If a plugin's `Transform` returns an error or `applied=false` for a given violation, the Engine stamps `REMEDIATION_CLASS_MANUAL_REVIEW` with `REMEDIATION_RESOLUTION_TRANSFORM_FAILED` on **that finding**. Transport failures (RPC raise) also drop the rule ID from further plugin Transform routing. Phase 4 (per-plugin AI batching) is **not** implemented yet: EXT- findings are excluded from the built-in mixed-node AI pass so they are not merged into built-in prompts.

### 5. AI escalation for plugin violations (Phase 4 — not implemented)

When Phase 4 ships, plugin violations that have no Transform (or whose Transform failed) enter a **separate** AI pass per plugin prefix. Until then, remaining ``EXT-`` findings are ``MANUAL_REVIEW``. Plugin violations require different handling than built-in violations because APME's AI prompts are tuned for built-in rules and have no domain knowledge about third-party checks.

#### AI guidance via violation metadata

Plugins supply AI context through the existing `Violation.metadata` map using a reserved key:

```
metadata["ai_guidance"] = "This violation fires when a play is missing a department tag. To fix it, add a 'tags' key to the play with at least one tag prefixed 'dept:'. Example: tags: [dept:platform]. The department must match one from the org's approved list."
```

The `ai_guidance` value is a free-form string containing whatever context the plugin author believes an AI agent needs to propose a fix: what the rule checks, how to resolve it, constraints, examples, or links to internal documentation. If `ai_guidance` is absent, the AI falls back to the violation's `message` and `level` fields; Tier 2 prompt templates will be extended to include `level` alongside `rule_id`, `line`, and `message` to support this behavior.

To support this end to end, the daemon's proto-to-dict conversion (see `src/apme_engine/daemon/violation_convert.py`) MUST be updated to preserve the `ai_guidance` metadata key (or all metadata for `EXT-` violations) instead of enforcing an allowlist that would drop it before Tier 2 escalation.

The SDK provides a convenience parameter:

```python
self.violation(
    rule_id="001",
    severity="high",
    message="Plays must have a department tag",
    file=node["file"],
    line=node["line"][0],
    ai_guidance="Add a 'tags' key with at least one dept: prefixed tag. "
    "Valid departments: platform, security, network, app.",
)
```

#### Per-plugin batching

Third-party violations are **not** combined with built-in violations during AI passes. The remediation engine partitions Tier 2 violations by origin:

- **Built-in violations** (L/M/R/P/SEC prefixes) — batched together and processed with APME's built-in AI prompts, as today.
- **Plugin violations** — grouped by plugin (by rule ID prefix, e.g., all `EXT-secteam-*` together). Each plugin group is processed as a **separate AI pass** using the `ai_guidance` from the violations' metadata.
- Violations from **different plugins** are never combined in the same AI pass — each plugin's domain context is distinct.

This separation ensures:

- Built-in AI prompts are not diluted by unrelated third-party context.
- Plugin-provided `ai_guidance` is used coherently within its own domain.
- AI proposals for plugin violations are attributable to the correct plugin for review.

### 6. Data contract

Plugins receive the following data in `ValidateRequest`:

| Field | Type | Description |
|-------|------|-------------|
| `files` | `repeated File` | Raw file content (path + bytes) |
| `hierarchy_payload` | `bytes` (JSON) | Structured Ansible tree: plays, tasks, modules, annotations, options |
| `ansible_core_version` | `string` | Target ansible-core version |
| `collection_specs` | `repeated string` | Collection specifiers from requirements.yml |
| `request_id` | `string` | Correlation ID |

The underlying `ValidateRequest` protobuf message also defines `scandata` as field 4. For calls from the Engine to plugins, the Engine **MUST** send `scandata` unset/empty, and plugin implementations **MUST** ignore any value present in this field. `scandata` contains Python-specific jsonpickle serialization of internal engine state and is **not** part of the stable, language-agnostic public contract, even though the field exists on the wire.

The `hierarchy_payload` JSON schema should be documented as a versioned public contract (future work — not blocking this ADR).

### 7. Python SDK (`apme-plugin-sdk`)

The plugin system is not viable without a low-friction authoring experience. The SDK is part of this decision, not a follow-up.

**`PluginBase` class** handles:

- Async gRPC server lifecycle (startup, graceful shutdown, signal handling)
- `Describe` response auto-generated from class attributes (`name`, `version`)
- `Health` endpoint (override ``PluginBase.health()``; default ``ok``. Example sidecars fail Health when the wrapped tool/bundle is missing.)
- `EXT-` prefix enforcement on rule IDs
- Proto serialization/deserialization (plugin author works with plain dicts and dataclasses)
- Configurable listen address via `APME_PLUGIN_LISTEN` env var (default `0.0.0.0:50100`). Plugin ports are **50100–50199** so they never collide with Engine `:50051`, validators `:50053–50059` / `:50062`, Gateway `:50060`, or Abbenay `:50057`.

**Plugin author implements two methods:**

- `validate(files, hierarchy) -> list[Violation]`
- `transform(file, violation) -> File | None`

**Minimal working plugin:**

```python
from apme_plugin_sdk import PluginBase


class MyOrgPlugin(PluginBase):
    name = "myorg"
    version = "1.0.0"

    def validate(self, files, hierarchy):
        violations = []
        for tree in hierarchy:
            for node in tree["nodes"]:
                if node["type"] == "play" and "dept" not in node.get("tags", []):
                    violations.append(
                        self.violation(
                            rule_id="001",
                            severity="warning",
                            message="Plays must have a department tag",
                            file=node["file"],
                            line=node["line"][0],
                            ai_guidance="Add a 'tags' key with at least one 'dept:'-prefixed tag. "
                            "Valid departments: platform, security, network, app.",
                        )
                    )
        return violations

    def transform_rule_ids(self):
        return [self.prefixed_id("001")]

    def transform(self, file_path, content, violation, hierarchy=None):
        del file_path, hierarchy
        if violation.rule_id != self.prefixed_id("001"):
            return None
        import yaml

        doc = yaml.safe_load(content.decode())
        if not isinstance(doc, dict):
            return None
        tags = doc.get("tags")
        if tags is None:
            doc["tags"] = ["dept:unassigned"]
        elif isinstance(tags, list) and not any(str(t).startswith("dept:") for t in tags):
            tags.append("dept:unassigned")
        else:
            return None
        return yaml.safe_dump(doc, default_flow_style=False).encode()


if __name__ == "__main__":
    MyOrgPlugin.serve()
```

**SDK dependencies:** `grpcio`, `grpcio-tools`, generated proto stubs. Does NOT depend on `apme-engine`.

**Template project:** A cookiecutter/copier template providing Containerfile, `pyproject.toml`, example plugin, Makefile with build/test/run targets.

### 8. Monorepo with shared proto

The SDK must share proto definitions with the main engine. The repo becomes multiroot:

```
apme-reviews/
  proto/                          # shared proto definitions (single source of truth)
    apme/v1/
      common.proto
      validate.proto
      engine.proto
      plugin.proto                # new
  src/
    apme_engine/                  # existing — main engine package
      pyproject.toml
    apme_plugin_sdk/              # new — lightweight SDK for plugin authors
      pyproject.toml
  scripts/
    gen_grpc.sh                   # updated — generates stubs for both packages
```

- Proto definitions are the contract boundary and must stay in sync between engine and SDK.
- Atomic commits prevent version skew (proto change + engine + SDK in one commit).
- Each package has its own `pyproject.toml` and independent version/release.
- `gen_grpc.sh` generates stubs into both packages.
- CI publishes packages independently — engine release does not force SDK release unless protos changed.

## Alternatives Considered

### Alternative 1: Rule-level extensibility

**Description**: Load custom Python `Rule` subclasses into the Native validator (via colon-separated `rules_dir`) or merge custom Rego files into the OPA bundle.

**Pros**:
- No new gRPC service needed
- Leverages existing rule infrastructure (match/process, OPA eval)
- Lower overhead per rule (no container)

**Cons**:
- Language-locked: Python for Native, Rego for OPA
- Security risk: arbitrary Python code runs inside a core service
- Detection and remediation are split — custom rules go in one place, custom transforms in another
- The existing `load_rules()` colon-separated path is fragile and undocumented
- No isolation between built-in and third-party code

**Why not chosen**: Mixes untrusted code into built-in services. Forces a language choice. Splits the detection/fix concern that third parties want to own together.

### Alternative 2: Extend the Validator service with a Transform RPC

**Description**: Add `Transform` to the existing `Validator` gRPC service. Third-party containers implement the extended Validator service. Built-in validators return `UNIMPLEMENTED` for `Transform`.

**Pros**:
- No new service definition
- Reuses existing discovery (env vars for validators)

**Cons**:
- Violates ADR-009: validators are read-only by design
- Muddies the built-in contract — built-in validators would have a vestigial `Transform` RPC
- Blurs the distinction between first-party validators and third-party plugins

**Why not chosen**: Contradicts the read-only validator principle. A separate `Plugin` service makes the extension point explicit and keeps the built-in Validator contract clean.

### Alternative 3: Two separate gRPC services on one container

**Description**: Plugin container exposes both `Validator` (existing service) and a new `Transformer` service on the same port.

**Pros**:
- Reuses existing Validator contract for detection
- Transformer service is independently versioned

**Cons**:
- Two service registrations to discover and manage
- More complex routing — Engine must track which address has a Validator vs which also has a Transformer
- Plugin author implements two separate service interfaces

**Why not chosen**: A single `Plugin` service is simpler to discover, document, and implement. One service, one address, one `Describe` call.

### Alternative 4: Separate SDK repo

**Description**: Publish the plugin SDK from a separate repository, with proto files copied or pulled via git submodule.

**Pros**:
- Independent release cycle
- Clean repo boundary

**Cons**:
- Proto drift risk between engine and SDK
- Git submodules are operationally painful
- Additional CI/CD infrastructure for a thin package

**Why not chosen**: The SDK is intentionally thin (base class + proto stubs). A separate repo adds overhead for a package that must stay in exact proto sync with the engine. Monorepo with shared `proto/` is simpler and safer.

## Consequences

### Positive

- Clean extension point: third parties bring a container, APME discovers and integrates it
- Language-agnostic contract: plugins can be written in any language with gRPC support
- Detection and remediation co-located: the plugin author owns both sides
- Low authoring friction via the Python SDK (~20 lines for a working plugin)
- No security risk to core services: plugins run in their own container/process
- Consistent with existing architecture (gRPC, env var discovery, Violation proto)

### Negative

- Additional container per plugin increases pod resource usage
- Plugin transforms are slower than built-in registry transforms (gRPC round-trip per violation vs in-process function call)
- Hierarchy JSON schema becomes a public contract that must be versioned carefully
- Monorepo adds build complexity (two packages, shared proto generation)

### Neutral

- Built-in validators and TransformRegistry are completely unchanged
- Plugin violations appear in the same output/report as built-in violations (distinguished by EXT- prefix)
- The convergence loop (scan -> fix -> rescan) works identically for plugin transforms
- AI escalation for plugin violations uses the same Tier 2 infrastructure but with plugin-supplied context instead of built-in prompts

## Implementation Notes

### Phase 1: Contract and SDK

1. Define `plugin.proto` with `Plugin` service, `TransformRequest`/`TransformResponse`, `DescribeRequest`/`DescribeResponse`
2. Create `src/apme_plugin_sdk/` with `PluginBase`, proto stubs, `pyproject.toml`
3. Update `scripts/gen_grpc.sh` to generate stubs for both packages
4. Write an example plugin (e.g., "all plays must have a department tag")

Local Podman images and `pod.yaml` containers for a private OPA bundle and
[ansible-security-scanner](https://github.com/cpeoples/ansible-security-scanner):
[PLUGIN_SIDECARS.md](../../docs/guides/PLUGIN_SIDECARS.md).

### Phase 2: Engine integration

1. Add `APME_PLUGIN_*_ADDRESS` env var scanning to `engine_server.py`
2. Call `Describe` on discovered plugins at startup
3. Fan out `Validate` calls to plugins alongside built-in validators
4. Build `rule_prefix -> plugin_address` routing map

### Phase 3: Remediation routing

1. Keep `EXT-` IDs as full strings (``normalize_rule_id`` lives in `rule_ids.py` and does not strip ``EXT-``)
2. Route `EXT-` violations with declared `transform_rule_ids` to plugin `Transform` RPC instead of the built-in registry
3. Integrate plugin transforms into the convergence loop
4. Update `launcher.py` if plugins should be discoverable in local daemon mode

### Phase 4: AI escalation

1. Partition Tier 2 violations by origin (built-in vs each plugin prefix)
2. Process each plugin's violations as a separate AI pass using `ai_guidance` from metadata
3. Construct plugin-specific AI prompts that include the `ai_guidance` strings
4. Ensure AI proposals for plugin violations are attributed to the originating plugin

### Phase 5: Developer experience

1. Publish `apme-plugin-sdk` to PyPI
2. Create cookiecutter/copier template repo
3. Document the hierarchy JSON schema as a versioned public contract
4. Write a "Building Your First APME Plugin" guide

## Related Decisions

- ADR-001: gRPC for all inter-service communication
- ADR-005: Env var discovery, no service discovery infrastructure
- ADR-007: Async gRPC servers (plugins should follow this pattern)
- ADR-008: Rule ID conventions — extended with `EXT-` prefix for plugins
- ADR-009: Validators are read-only; remediation is separate (plugins are exempt as they own both sides)
- ADR-025: AI provider protocol — plugin AI escalation uses the same Tier 2 infrastructure
- ADR-026: Rule scope metadata — plugins should set `scope` on violations
- Policy Library Adapters (proposed, not yet in-repo) — a complementary approach for consuming external Rego policy libraries (e.g., AAC, ansible-policy, STIG) *within* the existing OPA validator via adapters, as opposed to ADR-042's separate-container model. The two do not overlap: plugin services own detection+remediation in a separate container; policy library adapters would perform schema translation and compliance mapping inside OPA. If this approach is pursued, it should be captured as a formal DR/ADR.

## References

- [docs/design/THIRD_PARTY_EXTENSIBILITY_OPTIONS.md](../../docs/design/THIRD_PARTY_EXTENSIBILITY_OPTIONS.md) — options briefing: current tool paths, strategic alternatives, phased outcomes, cross-ADR tensions
- `proto/apme/v1/validate.proto` — existing Validator service contract
- `proto/apme/v1/common.proto` — shared Violation, File, Health types
- `src/apme_engine/daemon/engine_server.py` — Engine fan-out and remediation orchestration
- `src/apme_engine/remediation/partition.py` — Rule ID normalization and tier classification
- `src/apme_engine/remediation/registry.py` — Built-in TransformRegistry
- [docs/guides/PLUGIN_SIDECARS.md](../../docs/guides/PLUGIN_SIDECARS.md) — build plugin images; add OPA / ansible-security-scanner containers to the Podman pod

---

## Revision History

| Date | Author | Change |
|------|--------|--------|
| 2026-03-20 | APME Team | Initial proposal |
| 2026-03-20 | APME Team | Add AI escalation: per-plugin batching and ai_guidance metadata |
| 2026-04-20 | APME Team | Link options briefing design doc in References |
| 2026-10-05 | APME Team | Accept for implementation: node-scoped Transform + apply_yaml (ADR-044); plugin ports 50100+ (not Gateway 50060); operator attach is `Apme.spec.plugins[]` ([apme-operator#39](https://github.com/ansible/apme-operator/issues/39)) |
| 2026-10-05 | APME Team | APME Engine: `plugin.proto`, SDK, discovery, Validate fan-out, EXT Transform routing |
| 2026-10-05 | APME Team | Document Podman plugin sidecar images (custom OPA + ansible-security-scanner) |
| 2026-10-05 | APME Team | Pin plugin identity to env token; retry Describe misses; strip Validate extras; bind file-scoped EXT- findings; stamp TRANSFORM_FAILED without mixed-node AI |
| 2026-10-05 | APME Team | Honest Phase 4 gap: EXT- remaining is manual review; Validate sends ansible_core_version/collection_specs; Transform sends hierarchy; Health is overridable |
| 2026-10-05 | APME Team | Pin unavailable findings on the ledger; Describe failures still Validate; exact path bind; demote finding not rule on applied=false |
