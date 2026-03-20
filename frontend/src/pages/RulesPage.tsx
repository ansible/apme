import { useEffect, useState } from "react";
import { listRules } from "../services/api";
import type { RuleOut } from "../types/api";

export function RulesPage() {
  const [rules, setRules] = useState<RuleOut[]>([]);
  const [filter, setFilter] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    listRules()
      .then((data) => setRules(data.rules))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const filtered = filter
    ? rules.filter(
        (r) =>
          r.rule_id.toLowerCase().includes(filter.toLowerCase()) ||
          r.description.toLowerCase().includes(filter.toLowerCase())
      )
    : rules;

  return (
    <>
      <header className="apme-page-header">
        <h1 className="apme-page-title">Rule Catalog</h1>
      </header>

      <div style={{ marginBottom: 16 }}>
        <input
          type="text"
          placeholder="Search rules..."
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          style={{
            padding: "8px 12px",
            background: "var(--apme-input-bg)",
            border: "1px solid var(--apme-border)",
            borderRadius: 4,
            color: "var(--apme-text-primary)",
            fontSize: 14,
            width: 320,
          }}
        />
      </div>

      {loading ? (
        <div className="apme-empty">Loading...</div>
      ) : filtered.length === 0 ? (
        <div className="apme-empty">No rules found.</div>
      ) : (
        <div className="apme-table-container">
          <table className="apme-data-table">
            <thead>
              <tr>
                <th>Rule ID</th>
                <th>Description</th>
                <th>Validator</th>
                <th>Fixable</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((rule) => (
                <tr key={rule.rule_id}>
                  <td className="apme-rule-id">{rule.rule_id}</td>
                  <td>{rule.description}</td>
                  <td>
                    <span className="apme-badge running">{rule.validator}</span>
                  </td>
                  <td>
                    {rule.fixable
                      ? <span className="apme-badge passed">Yes</span>
                      : <span style={{ color: "#6a6e73" }}>—</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
