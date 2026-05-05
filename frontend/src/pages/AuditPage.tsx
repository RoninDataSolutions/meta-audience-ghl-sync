import { useState, useEffect, useCallback, useRef } from "react";
import type { AdAccount, AuditReport, AuditReportDetail } from "../types";
import type { AuditContext } from "../types";
import {
  getAuditReports,
  getAuditReport,
  triggerAudit,
  deleteAuditReport,
  reanalyzeAudit,
  addAuditContext,
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

      {analysis.projection_30d && !analysis.projection_30d.error && (
        <ProjectionSection projection={analysis.projection_30d} />
      )}

      {analysis.action_plan && !analysis.action_plan.error && (
        <ActionPlanSection plan={analysis.action_plan} />
      )}
    </div>
  );
}

// ── 30-Day Projection ────────────────────────────────────────────────────────

const TRAJECTORY_COLORS: Record<string, { bg: string; text: string }> = {
  improving: { bg: "rgba(16,163,127,0.15)", text: "#10a37f" },
  declining:  { bg: "rgba(233,69,96,0.15)",  text: "#e94560" },
  stable:     { bg: "rgba(59,130,246,0.15)", text: "#3b82f6" },
  volatile:   { bg: "rgba(217,119,6,0.15)",  text: "#d97706" },
};

function ProjectionSection({ projection }: { projection: any }) {
  const traj = (projection.trajectory || "").toLowerCase();
  const colors = TRAJECTORY_COLORS[traj] || { bg: "rgba(255,255,255,0.05)", text: "var(--text-muted)" };

  return (
    <AnalysisSection title="30-Day Projection">
      <div style={{ display: "flex", alignItems: "center", gap: "0.75rem", marginBottom: "0.75rem", flexWrap: "wrap" }}>
        {traj && (
          <span style={{
            background: colors.bg,
            color: colors.text,
            padding: "3px 10px",
            borderRadius: "4px",
            fontSize: "0.75rem",
            fontWeight: 700,
            textTransform: "uppercase",
            letterSpacing: "0.04em",
          }}>
            {traj}
          </span>
        )}
        {projection.confidence && (
          <span style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}>
            Confidence: <strong>{projection.confidence}</strong>
            {projection.confidence_note ? ` — ${projection.confidence_note}` : ""}
          </span>
        )}
      </div>

      {projection.summary && <p style={{ lineHeight: 1.6, marginBottom: "0.75rem" }}>{projection.summary}</p>}

      {/* Projected metrics */}
      {(projection.projected_spend != null || projection.projected_conversions != null) && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(130px, 1fr))", gap: "0.5rem", marginBottom: "0.75rem" }}>
          {[
            { label: "Spend (30d)", value: projection.projected_spend != null ? `$${Number(projection.projected_spend).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : null },
            { label: "Conversions", value: projection.projected_conversions != null ? String(Math.round(projection.projected_conversions)) : null },
            { label: "CPA",         value: projection.projected_cpa != null ? `$${Number(projection.projected_cpa).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : null },
            { label: "ROAS",        value: projection.projected_roas != null ? `${Number(projection.projected_roas).toFixed(2)}x` : null },
          ].filter(m => m.value !== null).map(({ label, value }) => (
            <div key={label} style={{ background: "var(--bg-card, rgba(255,255,255,0.05))", borderRadius: "6px", padding: "0.6rem 0.75rem" }}>
              <div style={{ fontSize: "0.7rem", color: "var(--text-muted)", marginBottom: "0.2rem" }}>{label}</div>
              <div style={{ fontSize: "1rem", fontWeight: 700 }}>{value}</div>
            </div>
          ))}
        </div>
      )}

      {projection.key_drivers?.length > 0 && (
        <div style={{ marginBottom: "0.75rem" }}>
          <strong style={{ fontSize: "0.8rem" }}>Key Drivers:</strong>
          <ul style={{ paddingLeft: "1.25rem", marginTop: "0.25rem", fontSize: "0.85rem" }}>
            {projection.key_drivers.map((d: string, i: number) => <li key={i}>{d}</li>)}
          </ul>
        </div>
      )}

      {(projection.upside_scenario || projection.downside_scenario) && (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.5rem" }}>
          {projection.upside_scenario && (
            <div style={{ background: "rgba(16,163,127,0.08)", border: "1px solid rgba(16,163,127,0.2)", borderRadius: "6px", padding: "0.6rem 0.75rem", fontSize: "0.8rem" }}>
              <div style={{ fontWeight: 600, color: "#10a37f", marginBottom: "0.25rem", fontSize: "0.75rem" }}>↑ UPSIDE</div>
              {projection.upside_scenario}
            </div>
          )}
          {projection.downside_scenario && (
            <div style={{ background: "rgba(233,69,96,0.08)", border: "1px solid rgba(233,69,96,0.2)", borderRadius: "6px", padding: "0.6rem 0.75rem", fontSize: "0.8rem" }}>
              <div style={{ fontWeight: 600, color: "#e94560", marginBottom: "0.25rem", fontSize: "0.75rem" }}>↓ DOWNSIDE</div>
              {projection.downside_scenario}
            </div>
          )}
        </div>
      )}
    </AnalysisSection>
  );
}

// ── Implementation Plan ──────────────────────────────────────────────────────

function ActionPlanSection({ plan }: { plan: any }) {
  return (
    <AnalysisSection title="Implementation Plan — Next 30 Days">
      {plan.executive_brief && (
        <p style={{ lineHeight: 1.6, marginBottom: "1rem" }}>{plan.executive_brief}</p>
      )}

      {plan.campaigns_to_create?.length > 0 && (
        <div style={{ marginBottom: "1rem" }}>
          <h5 style={{ fontSize: "0.8rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.04em", color: "var(--text-muted)", marginBottom: "0.5rem" }}>
            Campaigns to Create
          </h5>
          {plan.campaigns_to_create.map((c: any, i: number) => (
            <div key={i} style={{ border: "1px solid var(--border, rgba(255,255,255,0.1))", borderRadius: "6px", padding: "0.75rem", marginBottom: "0.5rem", background: "var(--bg-card, rgba(255,255,255,0.03))" }}>
              <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginBottom: "0.4rem", flexWrap: "wrap" }}>
                <span style={{ background: "#3b82f6", color: "white", borderRadius: "50%", width: "20px", height: "20px", display: "inline-flex", alignItems: "center", justifyContent: "center", fontSize: "0.7rem", fontWeight: 700, flexShrink: 0 }}>
                  {c.priority}
                </span>
                <span style={{ fontWeight: 600 }}>{c.name}</span>
                {c.objective && <span style={{ fontSize: "0.7rem", color: "var(--text-muted)", background: "rgba(255,255,255,0.07)", padding: "1px 6px", borderRadius: "3px" }}>{c.objective}</span>}
                {c.daily_budget && <span style={{ fontSize: "0.75rem", color: "#10a37f", fontWeight: 600 }}>{c.daily_budget}</span>}
              </div>
              {c.audience && <div style={{ fontSize: "0.8rem", marginBottom: "0.3rem" }}><strong>Audience:</strong> {c.audience}</div>}
              {c.creative_direction && (
                <div style={{ fontSize: "0.8rem", background: "rgba(59,130,246,0.08)", borderRadius: "4px", padding: "0.4rem 0.6rem", marginBottom: "0.3rem" }}>
                  <strong>Creative:</strong> {c.creative_direction}
                </div>
              )}
              {c.expected_result && <div style={{ fontSize: "0.8rem", color: "#10a37f" }}>→ {c.expected_result}</div>}
            </div>
          ))}
        </div>
      )}

      {[
        { label: "Campaigns to Cut",   key: "campaigns_to_cut",   color: "#e94560" },
        { label: "Audiences to Build", key: "audiences_to_build", color: "#3b82f6" },
        { label: "Budget Moves",       key: "budget_moves",       color: "#d97706" },
      ].map(({ label, key, color }) =>
        plan[key]?.length > 0 ? (
          <div key={key} style={{ marginBottom: "0.75rem" }}>
            <h5 style={{ fontSize: "0.8rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.04em", color: "var(--text-muted)", marginBottom: "0.4rem" }}>{label}</h5>
            {plan[key].map((item: string, i: number) => (
              <div key={i} style={{ borderLeft: `3px solid ${color}`, paddingLeft: "0.6rem", marginBottom: "0.3rem", fontSize: "0.85rem" }}>{item}</div>
            ))}
          </div>
        ) : null
      )}

      {plan.week_by_week?.length > 0 && (
        <div>
          <h5 style={{ fontSize: "0.8rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.04em", color: "var(--text-muted)", marginBottom: "0.5rem" }}>
            Week-by-Week Roadmap
          </h5>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))", gap: "0.5rem" }}>
            {plan.week_by_week.map((w: any, i: number) => (
              <div key={i} style={{ background: "var(--bg-card, rgba(255,255,255,0.05))", borderRadius: "6px", padding: "0.6rem 0.75rem" }}>
                <div style={{ fontWeight: 700, fontSize: "0.75rem", color: "var(--primary, #3b82f6)", marginBottom: "0.35rem", textTransform: "uppercase" }}>{w.week}</div>
                <ul style={{ paddingLeft: "1rem", margin: 0, fontSize: "0.8rem" }}>
                  {(w.actions || []).map((a: string, j: number) => <li key={j} style={{ marginBottom: "0.2rem" }}>{a}</li>)}
                </ul>
              </div>
            ))}
          </div>
        </div>
      )}
    </AnalysisSection>
  );
}

// ── Audit contexts panel ─────────────────────────────────────────────────────

function AuditContextsPanel({
  reportId,
  contexts,
  legacyNote,
  onChange,
}: {
  reportId: number;
  contexts: AuditContext[];
  legacyNote: string | null;
  onChange: (updated: AuditContext[]) => void;
}) {
  const [text, setText] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const allEntries: AuditContext[] = contexts.length > 0
    ? contexts
    : legacyNote
      ? [{ text: legacyNote, added_at: "" }]
      : [];

  const handleAdd = async () => {
    if (!text.trim()) return;
    setSaving(true);
    setError("");
    try {
      const result = await addAuditContext(reportId, text.trim());
      setText("");
      onChange(result.audit_contexts);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div style={{ marginBottom: "1.25rem" }}>
      <div style={{ fontSize: "0.75rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--primary, #3b82f6)", marginBottom: "0.5rem" }}>
        Audit Context
      </div>

      {allEntries.length === 0 ? (
        <div style={{ fontSize: "0.82rem", color: "var(--text-muted)", marginBottom: "0.75rem" }}>
          No context added yet.
        </div>
      ) : (
        <div style={{ marginBottom: "0.75rem" }}>
          {allEntries.map((c, i) => (
            <div
              key={i}
              style={{
                background: "rgba(59,130,246,0.06)",
                border: "1px solid rgba(59,130,246,0.15)",
                borderRadius: "6px",
                padding: "0.6rem 0.85rem",
                marginBottom: "0.4rem",
                fontSize: "0.85rem",
                lineHeight: 1.55,
              }}
            >
              {c.added_at && (
                <div style={{ fontSize: "0.7rem", color: "var(--text-muted)", marginBottom: "0.2rem" }}>
                  {new Date(c.added_at).toLocaleString()}
                </div>
              )}
              <div style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>{c.text}</div>
            </div>
          ))}
        </div>
      )}

      <div style={{ display: "flex", gap: "0.5rem", alignItems: "flex-start" }}>
        <textarea
          className="form-input"
          style={{ flex: 1, minHeight: "60px", resize: "vertical", fontSize: "0.85rem" }}
          placeholder="Add context for this report — what's happening with the business, seasonal notes, recent changes…"
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
        <button
          className="btn btn-primary btn-sm"
          onClick={handleAdd}
          disabled={saving || !text.trim()}
          style={{ whiteSpace: "nowrap", marginTop: "2px" }}
        >
          {saving ? "Saving…" : "+ Add"}
        </button>
      </div>
      {error && <div style={{ color: "var(--danger, #e94560)", fontSize: "0.82rem", marginTop: "0.3rem" }}>{error}</div>}
    </div>
  );
}

// ── Report detail panel ──────────────────────────────────────────────────────

function ReportDetail({ reportId, onClose }: { reportId: number; onClose: () => void }) {
  const [detail, setDetail] = useState<AuditReportDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [activeModel, setActiveModel] = useState<string>("");
  const [contexts, setContexts] = useState<AuditContext[]>([]);
  const [reanalyzing, setReanalyzing] = useState(false);
  const [reanalyzeError, setReanalyzeError] = useState("");

  const statusRef = useRef<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const d = await getAuditReport(reportId);
        if (!cancelled) {
          statusRef.current = d.status;
          setDetail(d);
          setContexts(d.audit_contexts || []);
          const firstModel = Object.keys(d.analyses || {})[0];
          if (firstModel) setActiveModel(firstModel);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    load();
    const interval = setInterval(() => {
      if (statusRef.current === "in_progress") load();
    }, 5000);
    return () => { cancelled = true; clearInterval(interval); };
  }, [reportId]);

  const handleReanalyze = async () => {
    if (!detail) return;
    setReanalyzeError("");
    setReanalyzing(true);
    try {
      await reanalyzeAudit(reportId, { models: ["claude_opus"] });
      setLoading(true);
      const d = await getAuditReport(reportId);
      statusRef.current = d.status;
      setDetail(d);
      setContexts(d.audit_contexts || []);
      setLoading(false);
    } catch (e: any) {
      setReanalyzeError(e.message);
    } finally {
      setReanalyzing(false);
    }
  };

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

      <AuditContextsPanel
        reportId={reportId}
        contexts={contexts}
        legacyNote={detail.report_notes}
        onChange={setContexts}
      />

      {/* Re-analyze controls */}
      {detail.status !== "in_progress" && (
        <div style={{ marginBottom: "1.25rem", display: "flex", alignItems: "center", gap: "0.75rem", flexWrap: "wrap" }}>
          <button
            className="btn btn-primary btn-sm"
            onClick={handleReanalyze}
            disabled={reanalyzing}
            title="Re-run AI analysis using stored Meta data — no new API calls"
          >
            {reanalyzing ? "Re-analyzing…" : "↺ Re-analyze with current context"}
          </button>
          <span style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}>
            Uses stored Meta data — no new API calls
          </span>
          {reanalyzeError && (
            <span style={{ color: "var(--danger, #e94560)", fontSize: "0.82rem" }}>{reanalyzeError}</span>
          )}
        </div>
      )}

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
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", borderBottom: "1px solid var(--border, rgba(255,255,255,0.1))", marginBottom: "1.25rem" }}>
            <div style={{ display: "flex", gap: "0.5rem" }}>
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
                  {m.replace("_", " ")} Analysis
                </button>
              ))}
            </div>
            <div style={{ display: "flex", gap: "0.4rem", paddingBottom: "2px" }}>
              {modelNames.map((m) => (
                <a
                  key={m}
                  className="btn btn-sm"
                  href={`/api/audit/reports/${reportId}/pdf?model=${m}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  title={`Download PDF for ${m} analysis`}
                >
                  {m.replace("_", " ")} PDF
                </a>
              ))}
            </div>
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

export default function AuditPage({ selectedAccount }: { selectedAccount: AdAccount | null }) {
  const [reports, setReports] = useState<AuditReport[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const limit = 10;

  const selectedAccountId = selectedAccount?.account_id ?? "";

  const [reportNotes, setReportNotes] = useState("");
  const [showReportNotes, setShowReportNotes] = useState(false);
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

  useEffect(() => { loadReports(); }, [loadReports]);

  // Reset offset when account changes
  useEffect(() => { setOffset(0); }, [selectedAccountId]);

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
      const result = await triggerAudit({
        account_id: selectedAccountId || undefined,
        models: ["claude_opus"],
        report_notes: reportNotes.trim() || undefined,
      });
      setReportNotes("");
      setShowReportNotes(false);
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

        <div style={{ display: "flex", gap: "1rem", flexWrap: "wrap", alignItems: "center" }}>
          <div style={{ fontSize: "0.85rem", color: "var(--text-muted)" }}>
            Account: <strong style={{ color: "var(--text)" }}>
              {selectedAccount ? selectedAccount.account_name : "Default (from .env)"}
            </strong>
            <span style={{ marginLeft: "0.5rem", fontSize: "0.75rem", color: "var(--primary)", opacity: 0.75 }}>
              · Claude Opus
            </span>
          </div>
          <button
            className="btn btn-primary"
            onClick={handleTrigger}
            disabled={triggering}
            style={{ marginLeft: "auto" }}
          >
            {triggering ? "Starting…" : "Run Audit"}
          </button>
        </div>

        {/* Audit context — collapsible */}
        <div style={{ marginTop: "0.75rem" }}>
          <button
            style={{ background: "none", border: "none", cursor: "pointer", fontSize: "0.8rem", color: "var(--text-muted)", padding: 0, display: "flex", alignItems: "center", gap: "0.3rem" }}
            onClick={() => setShowReportNotes((v) => !v)}
          >
            <span style={{ fontSize: "0.7rem" }}>{showReportNotes ? "▼" : "▶"}</span>
            Add audit context <span style={{ color: "var(--text-muted)", fontWeight: 400 }}>— what happened this period the AI should know about</span>
          </button>
          {showReportNotes && (
            <div style={{ marginTop: "0.5rem" }}>
              <textarea
                className="form-input"
                style={{ minHeight: "90px", resize: "vertical", fontSize: "0.85rem" }}
                placeholder={
                  "e.g. Launched a 10-class intro package at $149 on May 1st. " +
                  "Ran a 20% flash sale on the 28th — will skew CPA low. " +
                  "Instructor Maria left; prenatal class count dropped from 4 to 3/week."
                }
                value={reportNotes}
                onChange={(e) => setReportNotes(e.target.value)}
              />
              <div style={{ fontSize: "0.72rem", color: "var(--text-muted)", marginTop: "0.25rem" }}>
                Saved with this report. Used by the AI to explain anomalies and ground period-specific projections.
              </div>
            </div>
          )}
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
