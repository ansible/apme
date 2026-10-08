# orgpolicy example plugin

Detection-only plugin that flags `community.general.*` task modules as
`EXT-orgpolicy-001`. There is no deterministic Transform. Remaining
findings are **manual review** until ADR-042 Phase 4 (per-plugin AI).

```bash
APME_PLUGIN_LISTEN=0.0.0.0:50100 python examples/plugins/orgpolicy/plugin.py
export APME_PLUGIN_ORGPOLICY_ADDRESS=127.0.0.1:50100
```
