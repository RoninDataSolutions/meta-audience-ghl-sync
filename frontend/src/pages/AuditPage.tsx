import { useState, useEffect, useCallback } from "react";
import type { AdAccount, AuditReport, AuditReportDetail } from "../types";
import {
  getAccounts,
  getAuditReports,
  getAuditReport,
  triggerAudit,
  deleteAuditReport,
} from "../api";

// ── Helpers ─────────────────────────────────────────────────────────────────

function fmt(v: number | null | undefined, prefix = "", suffix = "", decimals = 2): string {
  if (v == null) return "—";
  return `${prefix}${v.toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals })}${suffix}`;
}

function fmtDate(s: string | null): string {
  if (!s) return "—";
  return new Date(s).toLocaleString();
}

function Delta({ pct, lowerIsBetter = false }: { pct: number | null; lowerIsBetter?: boolean }) {
  if (pct == null) return <span>—</span>;
  const positive = pct > 0;
  const good = lowerIsBetter ? !positive : positive;
  const color = good ? "var(--success, #10a37f)" : "var(--danger, #e94560)";
  const arrow = positive ? "↑" : "↓";
  return <span style={{ color, fontWeight: 600 }}>{arrow}{Math.abs(pct).toFixed(1)}%</span>;
}

// ── Report list row ──────────────────────────────────────────────────────────

function ReportRow({
  report,
  onView,
  onDelete,
  onRegenerate,
}: {
  report: AuditReport;
  onView: () => void;
  onDelete: () => void;
  onRegenerate: () => void;
}) {
  const statusColor = report.status === "completed" ? "var(--success, #10a37f)"
    : report.status === "failed" ? "var(--danger, #e94560)"
    : "var(--warning, #d97706)";

  return (
    <tr>
      <td>
        <div style={{ fontWeight: 600 }}>{report.account_name || report.account_id}</div>
        <div style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}>{report.account_id}</div>
      </td>
      <td style={{ fontSize: "0.85rem" }}>{fmtDate(report.generated_at)}</td>
      <td style={{ color: statusColor, fontWeight: 600, textTransform: "capitalize" }}>
        {report.status === "in_progress" ? "⏳ Running…" : report.status}
      </td>
      <td>{fmt(report.total_spend_7d, "$")}</td>
      <td>{fmt(report.total_spend_30d, "$")}</td>
      <td>{report.total_conversions_30d ?? "—"}</td>
      <td>{fmt(report.avg_cpa_30d, "$")}</td>
      <td>{report.avg_roas_30d ? `${report.avg_roas_30d.toFixed(2)}x` : "—"}</td>
      <td style={{ fontSize: "0.8rem" }}>{report.models_used || "—"}</td>
      <td>
        <div style={{ display: "flex", gap: "0.4rem", flexWrap: "wrap" }}>
          <button className="btn btn-sm" onClick={onView}>View</button>
          {report.has_pdf && (
            <a
              className="btn btn-sm"
              href={`/api/audit/reports/${report.id}/pdf`}
              target="_blank"
              rel="noopener noreferrer"
            >
              PDF
            </a>
          )}
          {report.status === "completed" && (
            <button className="btn btn-sm" onClick={onRegenerate} title="Re-render PDF with latest template">
              Regen PDF
            </button>
          )}
          <a
            className="btn btn-sm"
            href={`/api/audit/reports/${report.id}/json`}
            target="_blank"
            rel="noopener noreferrer"
          >
            JSON
          </a>
          <button className="btn btn-sm btn-danger" onClick={onDelete}>Delete</button>
        </div>
      </td>
    </tr>
  );
}

// ── Analysis tab renderer ────────────────────────────────────────────────────

function AnalysisSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: "1.5rem" }}>
      <h4 style={{ color: "var(--primary, #3b82f6)", marginBottom: "0.5rem", textTransform: "uppercase", fontSize: "0.8rem", letterSpacing: "0.05em" }}>{title}</h4>
      {children}
    </div>
  );
}

function FindingCard({ item, color }: { item: any; color: string }) {
  return (
    <div style={{ borderLeft: `3px solid ${color}`, paddingLeft: "0.75rem", marginBottom: "0.75rem" }}>
      <div style={{ fontWeight: 600 }}>{item.finding || item.opportunity}</div>
      {item.evidence && <div style={{ fontSize: "0.85rem", marginTop: "0.25rem" }}><em>Evidence:</em> {item.evidence}</div>}
      {item.rationale && <div style={{ fontSize: "0.85rem", marginTop: "0.25rem" }}><em>Rationale:</em> {item.rationale}</div>}
      {(item.recommendation || item.expected_impact) && (
        <div style={{ fontSize: "0.85rem", marginTop: "0.25rem", color: "var(--text-muted)" }}>
          → {item.recommendation || item.expected_impact}
        </div>
      )}
    </div>
  );
}

function VerdictBadge({ verdict }: { verdict: string }) {
  const colors: Record<string, string> = {
    strong: "#10a37f",
    decent: "#3b82f6",
    underperforming: "#d97706",
    critical: "#e94560",
  };
  return (
    <span style={{
      background: colors[verdict] || "#6c757d",
      color: "white",
      padding: "2px 8px",
      borderRadius: "4px",
      fontSize: "0.75rem",
      fontWeight: 600,
      textTransform: "capitalize",
    }}>
      {verdict}
    </span>
  );
}

function ModelAnalysis({ analysis }: { analysis: any }) {
  if (!analysis || analysis.error) {
    return <div style={{ color: "var(--danger, #e94560)" }}>Analysis failed: {analysis?.error || "Unknown error"}</div>;
  }

  return (
    <div>
      <AnalysisSection title="Executive Summary">
        <p style={{ lineHeight: 1.6 }}>{analysis.executive_summary}</p>
      </AnalysisSection>

      {analysis.campaign_by_campaign?.length > 0 && (
        <AnalysisSection title="Campaign Verdicts">
          {analysis.campaign_by_campaign.map((c: any, i: number) => (
            <div key={i} style={{ marginBottom: "1rem", padding: "0.75rem", background: "var(--bg-card, rgba(255,255,255,0.05))", borderRadius: "6px" }}>
              <div style={{ display: "flex", alignItems: "center", gap: "0.75rem", marginBottom: "0.5rem" }}>
                <span style={{ fontWeight: 600 }}>{c.campaign_name}</span>
                <span style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}>{c.objective}</span>
                <VerdictBadge verdict={c.verdict} />
              </div>
              <div style={{ fontSize: "0.85rem" }}>{c.summary}</div>
              <div style={{ fontSize: "0.8rem", marginTop: "0.4rem", color: "var(--text-muted)" }}>{c.key_metrics}</div>
              <div style={{ fontSize: "0.85rem", marginTop: "0.4rem", fontStyle: "italic" }}>→ {c.recommendation}</div>
            </div>
          ))}
        </AnalysisSection>
      )}

      {analysis.whats_working?.length > 0 && (
        <AnalysisSection title="What's Working">
          {analysis.whats_working.map((item: any, i: number) => (
            <FindingCard key={i} item={item} color="#10a37f" />
          ))}
        </AnalysisSection>
      )}

      {analysis.whats_not_working?.length > 0 && (
        <AnalysisSection title="What's Not Working">
          {analysis.whats_not_working.map((item: any, i: number) => (
            <FindingCard key={i} item={item} color="#e94560" />
          ))}
        </AnalysisSection>
      )}

      {analysis.opportunities?.length > 0 && (
        <AnalysisSection title="Opportunities">
          {analysis.opportunities.map((item: any, i: number) => (
            <FindingCard key={i} item={item} color="#3b82f6" />
          ))}
        </AnalysisSection>
      )}

      {analysis.creative_analysis && (
        <AnalysisSection title="Creative Analysis">
          <p style={{ marginBottom: "0.75rem" }}>{analysis.creative_analysis.summary}</p>
          {analysis.creative_analysis.fatigue_signals?.length > 0 && (
            <div style={{ marginBottom: "0.75rem" }}>
              <strong style={{ fontSize: "0.85rem" }}>Fatigue Signals:</strong>
              {analysis.creative_analysis.fatigue_signals.map((s: any, i: number) => (
                <div key={i} style={{ background: "rgba(217,119,6,0.15)", border: "1px solid #d97706", borderRadius: "4px", padding: "0.4rem 0.6rem", marginTop: "0.4rem", fontSize: "0.85rem" }}>
                  ⚠ <strong>{s.ad_or_adset}</strong>: {s.signal} → {s.action}
                </div>
              ))}
            </div>
          )}
          {analysis.creative_analysis.recommendations?.length > 0 && (
            <ol style={{ paddingLeft: "1.25rem", fontSize: "0.85rem" }}>
              {analysis.creative_analysis.recommendations.map((r: string, i: number) => <li key={i}>{r}</li>)}
            </ol>
          )}
        </AnalysisSection>
      )}

      {analysis.placement_analysis && (
        <AnalysisSection title="Placement Analysis">
          <p>{analysis.placement_analysis.summary}</p>
        </AnalysisSection>
      )}

      {analysis.demographic_analysis && (
        <AnalysisSection title="Demographic Analysis">
          <p>{analysis.demographic_analysis.summary}</p>
        </AnalysisSection>
      )}

      {analysis.trend_analysis && (
        <AnalysisSection title="Trend Analysis">
          <div style={{ marginBottom: "0.5rem" }}><strong>This week vs 30d:</strong> {analysis.trend_analysis.seven_vs_thirty}</div>
          <div style={{ marginBottom: "0.5rem" }}><strong>Long-term:</strong> {analysis.trend_analysis.thirty_vs_sixty_vs_ninety}</div>
          <div><strong>Frequency:</strong> {analysis.trend_analysis.frequency_trends}</div>
        </AnalysisSection>
      )}

      {analysis.risk_flags?.length > 0 && (
        <AnalysisSection title="Risk Flags">
          {analysis.risk_flags.map((flag: string, i: number) => (
            <div key={i} style={{ color: "#d97706", marginBottom: "0.3rem" }}>⚠ {flag}</div>
          ))}
        </AnalysisSection>
      )}

      {analysis.priority_actions?.length > 0 && (
        <AnalysisSection title="Priority Actions This Week">
          <ol style={{ paddingLeft: "1.25rem" }}>
            {analysis.priority_actions.map((action: string, i: number) => (
              <li key={i} style={{ marginBottom: "0.4rem" }}>{action}</li>
            ))}
          </ol>
        </AnalysisSection>
      )}
    </div>
  );
}

// ── Report detail panel ──────────────────────────────────────────────────────

function ReportDetail({ reportId, onClose }: { reportId: number; onClose: () => void }) {
  const [detail, setDetail] = useState<AuditReportDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [activeModel, setActiveModel] = useState<string>("");

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const d = await getAuditReport(reportId);
        if (!cancelled) {
          setDetail(d);
          const firstModel = Object.keys(d.analyses || {})[0];
          if (firstModel) setActiveModel(firstModel);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    load();
    // Poll if still in_progress
    const interval = setInterval(async () => {
      if (detail?.status === "in_progress") load();
    }, 5000);
    return () => { cancelled = true; clearInterval(interval); };
  }, [reportId]);

  if (loading) return <div className="card" style={{ padding: "2rem" }}>Loading report…</div>;
  if (!detail) return null;

  const modelNames = Object.keys(detail.analyses || {});

  return (
    <div className="card" style={{ marginTop: "1.5rem" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "1rem" }}>
        <div>
          <h3 style={{ margin: 0 }}>{detail.account_name || detail.account_id}</h3>
          <div style={{ fontSize: "0.85rem", color: "var(--text-muted)" }}>{fmtDate(detail.generated_at)} · {detail.models_used}</div>
        </div>
        <button className="btn" onClick={onClose}>✕ Close</button>
      </div>

      {/* Snapshot metrics */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(140px, 1fr))", gap: "0.75rem", marginBottom: "1.5rem" }}>
        {[
          { label: "Spend (7d)", value: fmt(detail.total_spend_7d, "$"), delta: detail.comparison?.deltas.spend_7d?.change_pct },
          { label: "Spend (30d)", value: fmt(detail.total_spend_30d, "$"), delta: detail.comparison?.deltas.spend_30d?.change_pct },
          { label: "Conv (30d)", value: String(detail.total_conversions_30d ?? "—"), delta: detail.comparison?.deltas.conversions_30d?.change_pct },
          { label: "Avg CPA", value: fmt(detail.avg_cpa_30d, "$"), delta: detail.comparison?.deltas.cpa_30d?.change_pct, lowerBetter: true },
          { label: "ROAS", value: detail.avg_roas_30d ? `${detail.avg_roas_30d.toFixed(2)}x` : "—", delta: detail.comparison?.deltas.roas_30d?.change_pct },
          { label: "CTR", value: fmt(detail.avg_ctr_30d, "", "%"), delta: detail.comparison?.deltas.ctr_30d?.change_pct },
          { label: "Campaigns", value: String(detail.campaign_count ?? "—") },
          { label: "Audiences", value: String(detail.audience_count ?? "—") },
        ].map(({ label, value, delta, lowerBetter }) => (
          <div key={label} style={{ background: "var(--bg-card, rgba(255,255,255,0.05))", borderRadius: "8px", padding: "0.75rem" }}>
            <div style={{ fontSize: "0.75rem", color: "var(--text-muted)", marginBottom: "0.25rem" }}>{label}</div>
            <div style={{ fontSize: "1.1rem", fontWeight: 700 }}>{value}</div>
            {delta != null && <Delta pct={delta} lowerIsBetter={lowerBetter} />}
          </div>
        ))}
      </div>

      {detail.status === "in_progress" && (
        <div style={{ textAlign: "center", padding: "2rem", color: "var(--text-muted)" }}>
          ⏳ Audit in progress… refreshing every 5 seconds
        </div>
      )}

      {detail.status === "failed" && (
        <div style={{ color: "var(--danger, #e94560)", padding: "1rem", background: "rgba(233,69,96,0.1)", borderRadius: "6px" }}>
          Audit failed: {detail.error_message}
        </div>
      )}

      {/* AI analysis tabs */}
      {modelNames.length > 0 && (
        <div>
          <div style={{ display: "flex", gap: "0.5rem", borderBottom: "1px solid var(--border, rgba(255,255,255,0.1))", marginBottom: "1.25rem" }}>
            {modelNames.map((m) => (
              <button
                key={m}
                onClick={() => setActiveModel(m)}
                style={{
                  padding: "0.5rem 1rem",
                  border: "none",
                  background: "none",
                  cursor: "pointer",
                  borderBottom: activeModel === m ? "2px solid var(--primary, #3b82f6)" : "2px solid transparent",
                  color: activeModel === m ? "var(--primary, #3b82f6)" : "var(--text-muted)",
                  fontWeight: activeModel === m ? 600 : 400,
                  textTransform: "capitalize",
                }}
              >
                {m} Analysis
              </button>
            ))}
          </div>
          {activeModel && detail.analyses[activeModel] && (
            <ModelAnalysis analysis={detail.analyses[activeModel]} />
          )}
        </div>
      )}
    </div>
  );
}

// ── Main page ────────────────────────────────────────────────────────────────

export default function AuditPage() {
  const [accounts, setAccounts] = useState<AdAccount[]>([]);
  const [reports, setReports] = useState<AuditReport[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const limit = 10;

  const [selectedAccountId, setSelectedAccountId] = useState<string>("");
  const [selectedModels, setSelectedModels] = useState({ claude: true, openai: false });
  const [triggering, setTriggering] = useState(false);
  const [viewingId, setViewingId] = useState<number | null>(null);
  const [error, setError] = useState("");

  const loadReports = useCallback(async () => {
    try {
      const data = await getAuditReports({ limit, offset, account_id: selectedAccountId || undefined });
      setReports(data.reports);
      setTotal(data.total);
    } catch (e: any) {
      setError(e.message);
    }
  }, [offset, selectedAccountId]);

  useEffect(() => {
    getAccounts().then((d) => setAccounts(d.accounts)).catch(() => {});
  }, []);

  useEffect(() => { loadReports(); }, [loadReports]);

  // Poll while any report is in_progress
  useEffect(() => {
    const hasRunning = reports.some((r) => r.status === "in_progress");
    if (!hasRunning) return;
    const id = setInterval(loadReports, 5000);
    return () => clearInterval(id);
  }, [reports, loadReports]);

  const handleTrigger = async () => {
    setError("");
    setTriggering(true);
    try {
      const models = [
        ...(selectedModels.claude ? ["claude"] : []),
        ...(selectedModels.openai ? ["openai"] : []),
      ];
      const result = await triggerAudit({
        account_id: selectedAccountId || undefined,
        models,
      });
      setViewingId(result.report_id);
      loadReports();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setTriggering(false);
    }
  };

  const handleDelete = async (id: number) => {
    if (!confirm("Delete this audit report?")) return;
    try {
      await deleteAuditReport(id);
      if (viewingId === id) setViewingId(null);
      loadReports();
    } catch (e: any) {
      alert(e.message);
    }
  };

  const handleRegenerate = async (id: number) => {
    try {
      const resp = await fetch(`/api/audit/reports/${id}/regenerate-pdf`, { method: "POST" });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: resp.statusText }));
        throw new Error(err.detail || resp.statusText);
      }
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `audit_${id}.pdf`;
      a.click();
      URL.revokeObjectURL(url);
      loadReports();
    } catch (e: any) {
      alert(`Regenerate failed: ${e.message}`);
    }
  };

  const totalPages = Math.ceil(total / limit);
  const page = Math.floor(offset / limit) + 1;

  return (
    <main className="dashboard">
      {/* Trigger bar */}
      <div className="card" style={{ marginBottom: "1.5rem" }}>
        <h2 style={{ marginBottom: "1rem" }}>Run Audit</h2>
        {error && <div className="error-banner" style={{ marginBottom: "1rem" }}>{error}</div>}

        <div style={{ display: "flex", gap: "1rem", flexWrap: "wrap", alignItems: "flex-end" }}>
          <div style={{ flex: 1, minWidth: "200px" }}>
            <label style={{ display: "block", fontSize: "0.85rem", marginBottom: "0.4rem", color: "var(--text-muted)" }}>
              Account
            </label>
            <select
              className="form-select"
              value={selectedAccountId}
              onChange={(e) => setSelectedAccountId(e.target.value)}
            >
              <option value="">Default (from .env)</option>
              {accounts.filter((a) => a.is_active).map((a) => (
                <option key={a.id} value={a.account_id}>{a.account_name}</option>
              ))}
            </select>
          </div>

          <div>
            <label style={{ display: "block", fontSize: "0.85rem", marginBottom: "0.4rem", color: "var(--text-muted)" }}>
              Models
            </label>
            <div style={{ display: "flex", gap: "0.75rem" }}>
              {(["claude", "openai"] as const).map((m) => (
                <label key={m} style={{ display: "flex", alignItems: "center", gap: "0.35rem", cursor: "pointer" }}>
                  <input
                    type="checkbox"
                    checked={selectedModels[m]}
                    onChange={(e) => setSelectedModels((s) => ({ ...s, [m]: e.target.checked }))}
                  />
                  <span style={{ textTransform: "capitalize" }}>{m}</span>
                </label>
              ))}
            </div>
          </div>

          <button
            className="btn btn-primary"
            onClick={handleTrigger}
            disabled={triggering || (!selectedModels.claude && !selectedModels.openai)}
          >
            {triggering ? "Starting…" : "Run Audit"}
          </button>
        </div>
      </div>

      {/* Report list */}
      <div className="card">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem" }}>
          <h2 style={{ margin: 0 }}>Audit History</h2>
          <span style={{ fontSize: "0.85rem", color: "var(--text-muted)" }}>{total} report{total !== 1 ? "s" : ""}</span>
        </div>

        {reports.length === 0 ? (
          <p style={{ color: "var(--text-muted)" }}>No audit reports yet. Run your first audit above.</p>
        ) : (
          <>
            <div style={{ overflowX: "auto" }}>
              <table className="history-table">
                <thead>
                  <tr>
                    <th>Account</th>
                    <th>Date</th>
                    <th>Status</th>
                    <th>Spend (7d)</th>
                    <th>Spend (30d)</th>
                    <th>Conv (30d)</th>
                    <th>CPA</th>
                    <th>ROAS</th>
                    <th>Models</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {reports.map((r) => (
                    <ReportRow
                      key={r.id}
                      report={r}
                      onView={() => setViewingId(r.id)}
                      onDelete={() => handleDelete(r.id)}
                      onRegenerate={() => handleRegenerate(r.id)}
                    />
                  ))}
                </tbody>
              </table>
            </div>

            {totalPages > 1 && (
              <div className="pagination" style={{ marginTop: "1rem" }}>
                <button className="btn" disabled={page <= 1} onClick={() => setOffset(offset - limit)}>← Prev</button>
                <span style={{ padding: "0 1rem", color: "var(--text-muted)" }}>Page {page} of {totalPages}</span>
                <button className="btn" disabled={page >= totalPages} onClick={() => setOffset(offset + limit)}>Next →</button>
              </div>
            )}
          </>
        )}
      </div>

      {/* Report detail */}
      {viewingId !== null && (
        <ReportDetail reportId={viewingId} onClose={() => setViewingId(null)} />
      )}
    </main>
  );
}
