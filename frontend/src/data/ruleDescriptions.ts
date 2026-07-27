import { apmeApiUrl, getApmeApiAdapter } from "../api/apmeApiAdapter";

/** Live descriptions populated from the Gateway /rules API. */
const _descriptions: Record<string, string> = {};

/**
 * Strip validator prefix (e.g. "native:L042" → "L042") for description lookup.
 */
export function bareRuleId(ruleId: string): string {
  const idx = ruleId.indexOf(":");
  if (idx > 0 && idx < ruleId.length - 1) return ruleId.slice(idx + 1);
  return ruleId;
}

/**
 * Look up a rule description, handling prefixed IDs like "native:L042".
 */
export function getRuleDescription(ruleId: string): string {
  return _descriptions[ruleId] ?? _descriptions[bareRuleId(ruleId)] ?? "";
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
