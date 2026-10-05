import { apmeApiUrl, bareRuleId, getApmeApiAdapter } from "../api/apmeApiAdapter";

export { bareRuleId };

/** Live descriptions populated from the Gateway /rules API. */
const _descriptions: Record<string, string> = {};

/**
 * Look up a rule description, handling legacy ``native:`` prefixed IDs only.
 *
 * Backend normalization strips only the ``native:`` prefix; broader
 * ``bareRuleId`` stripping would map unrelated IDs (e.g. ``SEC:L001`` →
 * ``L001``).
 */
export function getRuleDescription(ruleId: string): string {
  const bareNative = ruleId.startsWith("native:")
    ? ruleId.slice("native:".length)
    : ruleId;
  return _descriptions[ruleId] ?? _descriptions[bareNative] ?? "";
}

let _fetchStarted = false;

function _loadFromApi(): void {
  if (_fetchStarted) return;
  _fetchStarted = true;
  const { fetch: doFetch } = getApmeApiAdapter();
  doFetch(apmeApiUrl("/rules"))
    .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
    .then((rows: { rule_id: string; description: string }[]) => {
      if (!Array.isArray(rows)) return;
      for (const r of rows) {
        if (r.rule_id && r.description) {
          _descriptions[r.rule_id] = r.description;
        }
      }
    })
    .catch((err) => {
      console.warn("Failed to load rule descriptions from /api/v1/rules:", err);
    });
}

_loadFromApi();
