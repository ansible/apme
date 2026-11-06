# REQ-022 Contract: Rule Results

## L037: Unresolved module

- **Source**: Ansible validator module and action plugin introspection, with
  each task's `collections_in_play` passed to Ansible's plugin loaders and
  project-level configuration loaded from the scan root.
- **Condition**: `find_plugin_with_context()` does not resolve the authored
  module and does not classify it as removed.
- **Output**: `rule_id=L037`, task scope, module name, file, line, and task path.
- **Valid short name**: No L037 result when ansible-core resolves it; M001 may
  separately recommend the canonical FQCN.
- **Removed module**: M004 remains authoritative and suppresses duplicate L037.

## R118: Inbound transfer annotation

- **Source**: OPA hierarchy serializer.
- **Output shape**: `type=risk_annotation`, `risk_type=inbound_transfer`, with
  `src` and optional `dest` location objects. URL userinfo, query, and fragment contents
  are redacted before serialization.
- **Profiles**: get_url, git, subversion, and remote URL unarchive, as currently
  defined by APME's built-in risk map and ARI lineage.
- **Unarchive gate**: `remote_src` must be true and `src` must be a remote URL
  or a template that may resolve to one. Local paths, unsupported URI schemes,
  and `file://` sources are excluded.

## New lint checks

New checks use unique L IDs (`L112`–`L114`), existing `GraphRuleResult`
metadata, and read-only scanning behavior. Their source lineage and generated
catalog entries are registered with the implementation.
