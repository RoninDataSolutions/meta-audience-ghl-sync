import { useState, useEffect, useCallback } from "react";
import type { AdAccount } from "../types";
import {
  getAuditReports,
  getAuditReport,
  generateHeatmap,
  listHeatmapSnapshots,
  getHeatmapSnapshot,
  deleteHeatmapSnapshot,
  type HeatmapSnapshotSummary,
} from "../api";
import GeographicHeatmap from "../components/GeographicHeatmap";

interface Props {
  selectedAccount: AdAccount | null;
}

interface DataSource {
  type: "audit" | "fresh" | "snapshot";
  generatedAt: string | null;
  reportId?: number;
  snapshotId?: number;
  days: number;
}

const RANGE_PRESETS = [30, 60, 90, 180, 365];

export default function HeatMapPage({ selectedAccount }: Props) {
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState("");
  const [geoData, setGeoData] = useState<any>(null);
  const [source, setSource] = useState<DataSource | null>(null);
  const [days, setDays] = useState<number>(30);
  const [customDays, setCustomDays] = useState<string>("");
  const [showHistory, setShowHistory] = useState(false);
  const [history, setHistory] = useState<HeatmapSnapshotSummary[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState("");

  const refreshHistory = useCallback(async () => {
    setHistoryLoading(true);
    setHistoryError("");
    try {
      const res = await listHeatmapSnapshots({
        account_id: selectedAccount?.account_id,
        limit: 50,
      });
      setHistory(res.snapshots);
    } catch (e: any) {
      setHistoryError(e.message || "Failed to load history");
    } finally {
      setHistoryLoading(false);
    }
  }, [selectedAccount?.account_id]);

  useEffect(() => {
    if (showHistory) refreshHistory();
  }, [showHistory, refreshHistory]);

  // Load the most recent audit's geographic data on mount / account change
  useEffect(() => {
    let cancelled = false;

    async function loadFromAudit() {
      setLoading(true);
      setError("");
      setGeoData(null);
      setSource(null);

      try {
        const list = await getAuditReports({
          limit: 10,
          account_id: selectedAccount?.account_id,
        });
        const completed = list.reports.find((r) => r.status === "completed");
        if (!completed) {
          if (!cancelled) setError("No completed audit found. Click 'Generate Heat Map' to compute one without running a full audit.");
          return;
        }

        const detail = await getAuditReport(completed.id);
        if (cancelled) return;

        const geo = detail.raw_metrics?.business_context?.geographic_breakdown;
        if (!geo?.states?.length) {
          setError("The most recent audit doesn't contain geographic data. Click 'Generate Heat Map' to compute it now.");
          return;
        }

        setGeoData(geo);
        const auditWindow = typeof geo.window_days === "number" ? geo.window_days : 30;
        setDays(auditWindow);
        setSource({ type: "audit", generatedAt: detail.generated_at, reportId: detail.id, days: auditWindow });
      } catch (e: any) {
        if (!cancelled) setError(e.message || "Failed to load heat map data");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    loadFromAudit();
    return () => { cancelled = true; };
  }, [selectedAccount?.account_id]);

  const handleGenerate = async (windowDays: number = days) => {
    setGenerating(true);
    setError("");
    try {
      const result = await generateHeatmap(selectedAccount?.account_id, windowDays);
      setGeoData(result.geographic_breakdown);
      setDays(result.days);
      setSource({
        type: "fresh",
        generatedAt: result.generated_at,
        days: result.days,
        snapshotId: result.snapshot_id ?? undefined,
      });
      if (showHistory) refreshHistory();
    } catch (e: any) {
      setError(e.message || "Heat map generation failed");
    } finally {
      setGenerating(false);
    }
  };

  const handleLoadSnapshot = async (id: number) => {
    setGenerating(true);
    setError("");
    try {
      const snap = await getHeatmapSnapshot(id);
      setGeoData(snap.geographic_breakdown);
      setDays(snap.days_back);
      setSource({
        type: "snapshot",
        generatedAt: snap.generated_at,
        days: snap.days_back,
        snapshotId: snap.id,
      });
    } catch (e: any) {
      setError(e.message || "Failed to load snapshot");
    } finally {
      setGenerating(false);
    }
  };

  const handleDeleteSnapshot = async (id: number) => {
    if (!confirm(`Delete snapshot #${id}? This cannot be undone.`)) return;
    try {
      await deleteHeatmapSnapshot(id);
      setHistory((prev) => prev.filter((s) => s.id !== id));
      if (source?.snapshotId === id) {
        setSource((prev) => (prev ? { ...prev, snapshotId: undefined } : prev));
      }
    } catch (e: any) {
      setHistoryError(e.message || "Failed to delete snapshot");
    }
  };

  const handlePresetClick = (preset: number) => {
    setDays(preset);
    setCustomDays("");
    handleGenerate(preset);
  };

  const handleCustomApply = () => {
    const n = parseInt(customDays, 10);
    if (!isNaN(n) && n > 0 && n <= 365) {
      setDays(n);
      handleGenerate(n);
    }
  };

  const windowLabel = (d: number): string => {
    if (d >= 365 && d % 365 === 0) return `${d / 365}y`;
    if (d >= 60 && d % 30 === 0) return `${d / 30}mo`;
    return `${d}d`;
  };

  const handleExportCSV = () => {
    if (!geoData?.states?.length) return;

    const win = source?.days ?? days;
    const accountLabel = selectedAccount?.account_name || selectedAccount?.account_id || "default";
    const today = new Date().toISOString().slice(0, 10);
    const filename = `heatmap_${accountLabel.replace(/[^a-z0-9]+/gi, "_")}_${windowLabel(win)}_${today}.csv`;

    const sum = geoData.summary || {};
    const narr = geoData.narrative || {};

    // Section 1: summary header rows
    const lines: string[] = [];
    lines.push(`# Heat Map Report`);
    lines.push(`# Account,${accountLabel}`);
    lines.push(`# Window,${windowLabel(win)} (${win} days)`);
    lines.push(`# Generated,${new Date().toISOString()}`);
    lines.push(`# Total Spend,$${sum.total_spend ?? 0}`);
    lines.push(`# Avg Monthly Spend,$${sum.avg_monthly_spend ?? 0}`);
    lines.push(`# Total LTV,$${sum.total_ltv ?? 0}`);
    lines.push(`# Window ROAS,${sum.account_roas ?? ""}`);
    lines.push(`# LTV ROAS,${sum.ltv_roas ?? ""}`);
    if (narr.inclusion_csv) lines.push(`# Inclusion List,"${narr.inclusion_csv}"`);
    if (narr.exclusion_csv) lines.push(`# Exclusion List,"${narr.exclusion_csv}"`);
    lines.push("");

    // Section 2: per-state rows
    const header = [
      "State", "State Code", "Classification",
      "Spend", "Impressions", "Clicks",
      "Contacts", "Paying Contacts",
      "Conversions", "Revenue (window)", "Total LTV", "Avg LTV",
      "CPA", "ROAS", "Conversion Rate %",
    ];
    lines.push(header.join(","));

    const escape = (v: any): string => {
      if (v == null) return "";
      const s = String(v);
      if (s.includes(",") || s.includes('"') || s.includes("\n")) {
        return `"${s.replace(/"/g, '""')}"`;
      }
      return s;
    };

    const sorted = [...geoData.states].sort((a: any, b: any) => (b.spend ?? 0) - (a.spend ?? 0));
    for (const s of sorted) {
      lines.push([
        s.state_name, s.state, s.classification ?? "",
        s.spend, s.impressions, s.clicks,
        s.contacts, s.paying_contacts,
        s.conversions, s.revenue_30d, s.total_ltv, s.avg_ltv ?? "",
        s.cpa ?? "", s.roas ?? "", s.conversion_rate_pct,
      ].map(escape).join(","));
    }

    // Section 3: narrative as a trailing block
    if (narr.summary) {
      lines.push("");
      lines.push("# Plain English Summary");
      for (const para of narr.summary.split("\n")) {
        lines.push(`# ${para}`);
      }
    }

    const blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  return (
    <main className="dashboard">
      <div className="card">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "1rem", gap: "1rem", flexWrap: "wrap" }}>
          <div style={{ flex: "1 1 360px" }}>
            <h2 style={{ margin: 0 }}>Heat Map — Geographic Performance</h2>
            <div style={{ fontSize: "0.85rem", color: "var(--text-muted)", marginTop: "0.25rem" }}>
              {selectedAccount ? `${selectedAccount.account_name} · ` : ""}
              Meta ad spend × GHL contacts × matched conversions × lifetime revenue, per US state
            </div>
            {source && (
              <div style={{ fontSize: "0.75rem", color: "var(--text-muted)", marginTop: "0.4rem" }}>
                {source.type === "audit"
                  ? `From audit #${source.reportId} · ${windowLabel(source.days)} window · ${source.generatedAt ? new Date(source.generatedAt).toLocaleString() : "—"}`
                  : source.type === "snapshot"
                  ? `Snapshot #${source.snapshotId} · ${windowLabel(source.days)} window · ${source.generatedAt ? new Date(source.generatedAt).toLocaleString() : "—"}`
                  : `Freshly generated · ${windowLabel(source.days)} window · ${source.generatedAt ? new Date(source.generatedAt).toLocaleString() : "—"}${source.snapshotId ? ` · saved as #${source.snapshotId}` : ""}`}
              </div>
            )}
          </div>
        </div>

        {/* Time range selector */}
        <div style={{
          display: "flex",
          alignItems: "center",
          gap: "0.5rem",
          flexWrap: "wrap",
          marginBottom: "1rem",
          padding: "0.75rem 1rem",
          background: "rgba(255,255,255,0.03)",
          border: "1px solid var(--border, rgba(255,255,255,0.08))",
          borderRadius: "8px",
        }}>
          <span style={{ fontSize: "0.8rem", color: "var(--text-muted)", fontWeight: 600, marginRight: "0.25rem" }}>
            Time window:
          </span>
          {RANGE_PRESETS.map((preset) => (
            <button
              key={preset}
              className="btn btn-sm"
              onClick={() => handlePresetClick(preset)}
              disabled={generating}
              style={{
                background: days === preset ? "var(--primary, #3b82f6)" : "transparent",
                color: days === preset ? "white" : "var(--text)",
                borderColor: days === preset ? "var(--primary, #3b82f6)" : "var(--border)",
                fontWeight: days === preset ? 600 : 400,
              }}
              title={preset === 30 ? "Recent activity" : preset === 90 ? "Quarterly view" : preset === 180 ? "Half-year trend" : preset === 365 ? "Yearly view" : ""}
            >
              {windowLabel(preset)}
            </button>
          ))}
          <div style={{ width: "1px", height: "20px", background: "var(--border)", margin: "0 0.25rem" }} />
          <span style={{ fontSize: "0.78rem", color: "var(--text-muted)" }}>Custom:</span>
          <input
            type="number"
            min={1}
            max={365}
            placeholder="days"
            value={customDays}
            onChange={(e) => setCustomDays(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") handleCustomApply(); }}
            disabled={generating}
            style={{ width: "70px", padding: "0.3rem 0.5rem", borderRadius: "4px", border: "1px solid var(--border)", background: "transparent", color: "var(--text)", fontSize: "0.85rem" }}
          />
          <button
            className="btn btn-sm"
            onClick={handleCustomApply}
            disabled={generating || !customDays.trim()}
          >
            Apply
          </button>
          <div style={{ flex: 1 }} />
          <a
            className="btn btn-sm"
            href={`/api/heatmap/pdf?${new URLSearchParams({
              ...(selectedAccount?.account_id ? { account_id: selectedAccount.account_id } : {}),
              days: String(source?.days ?? days),
            }).toString()}`}
            target="_blank"
            rel="noopener noreferrer"
            style={geoData?.states?.length ? {} : { pointerEvents: "none", opacity: 0.4 }}
            title="Download a structured PDF report (cover + map + tiers + actions + appendix)"
          >
            ⎙ Export PDF
          </a>
          <button
            className="btn btn-sm"
            onClick={handleExportCSV}
            disabled={!geoData?.states?.length}
            title="Download CSV report (summary + per-state table + plain-English narrative)"
          >
            ⬇ Export CSV
          </button>
          <button
            className="btn btn-sm"
            onClick={() => setShowHistory((v) => !v)}
            title="Show stored heat map snapshots"
            style={{
              background: showHistory ? "var(--primary, #3b82f6)" : "transparent",
              color: showHistory ? "white" : "var(--text)",
            }}
          >
            🕓 History
          </button>
          <button
            className="btn btn-primary"
            onClick={() => handleGenerate()}
            disabled={generating}
            title="Refresh the heat map with current window (~10-20s)"
          >
            {generating ? "Generating…" : "↻ Generate"}
          </button>
        </div>

        {showHistory && (
          <div style={{
            marginBottom: "1rem",
            padding: "0.75rem 1rem",
            background: "rgba(255,255,255,0.02)",
            border: "1px solid var(--border, rgba(255,255,255,0.08))",
            borderRadius: "8px",
          }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "0.5rem" }}>
              <h3 style={{ margin: 0, fontSize: "0.95rem" }}>
                Snapshot History {history.length > 0 && <span style={{ color: "var(--text-muted)", fontWeight: 400 }}>· {history.length}</span>}
              </h3>
              <button className="btn btn-sm" onClick={refreshHistory} disabled={historyLoading}>
                {historyLoading ? "Loading…" : "↻ Refresh"}
              </button>
            </div>
            {historyError && (
              <div style={{ padding: "0.5rem 0.75rem", background: "rgba(217,119,6,0.12)", border: "1px solid rgba(217,119,6,0.35)", borderRadius: "6px", marginBottom: "0.5rem", fontSize: "0.85rem" }}>
                {historyError}
              </div>
            )}
            {!historyLoading && history.length === 0 && !historyError && (
              <div style={{ padding: "0.5rem", color: "var(--text-muted)", fontSize: "0.85rem" }}>
                No stored snapshots yet. Generate a heat map to create one.
              </div>
            )}
            {history.length > 0 && (
              <div style={{ overflowX: "auto" }}>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.82rem" }}>
                  <thead>
                    <tr style={{ textAlign: "left", color: "var(--text-muted)", borderBottom: "1px solid var(--border)" }}>
                      <th style={{ padding: "0.4rem 0.5rem" }}>#</th>
                      <th style={{ padding: "0.4rem 0.5rem" }}>Generated</th>
                      <th style={{ padding: "0.4rem 0.5rem" }}>Account</th>
                      <th style={{ padding: "0.4rem 0.5rem" }}>Window</th>
                      <th style={{ padding: "0.4rem 0.5rem" }}>Source</th>
                      <th style={{ padding: "0.4rem 0.5rem", textAlign: "right" }}>Spend</th>
                      <th style={{ padding: "0.4rem 0.5rem", textAlign: "right" }}>LTV</th>
                      <th style={{ padding: "0.4rem 0.5rem", textAlign: "right" }}>ROAS</th>
                      <th style={{ padding: "0.4rem 0.5rem", textAlign: "right" }}>States</th>
                      <th style={{ padding: "0.4rem 0.5rem", textAlign: "right" }}>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {history.map((s) => {
                      const isCurrent = source?.snapshotId === s.id;
                      return (
                        <tr key={s.id} style={{
                          borderBottom: "1px solid var(--border, rgba(255,255,255,0.05))",
                          background: isCurrent ? "rgba(59,130,246,0.08)" : "transparent",
                        }}>
                          <td style={{ padding: "0.4rem 0.5rem", fontFamily: "var(--font-mono, monospace)" }}>{s.id}</td>
                          <td style={{ padding: "0.4rem 0.5rem" }}>
                            {s.generated_at ? new Date(s.generated_at).toLocaleString() : "—"}
                          </td>
                          <td style={{ padding: "0.4rem 0.5rem" }}>{s.account_name || s.account_id}</td>
                          <td style={{ padding: "0.4rem 0.5rem" }}>{windowLabel(s.days_back)}</td>
                          <td style={{ padding: "0.4rem 0.5rem", color: "var(--text-muted)" }}>{s.source}</td>
                          <td style={{ padding: "0.4rem 0.5rem", textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
                            {s.total_spend != null ? `$${s.total_spend.toLocaleString(undefined, { maximumFractionDigits: 0 })}` : "—"}
                          </td>
                          <td style={{ padding: "0.4rem 0.5rem", textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
                            {s.total_ltv != null ? `$${s.total_ltv.toLocaleString(undefined, { maximumFractionDigits: 0 })}` : "—"}
                          </td>
                          <td style={{ padding: "0.4rem 0.5rem", textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
                            {s.ltv_roas != null ? `${s.ltv_roas.toFixed(2)}x` : "—"}
                          </td>
                          <td style={{ padding: "0.4rem 0.5rem", textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
                            {s.states_with_paying ?? 0}/{s.states_with_spend ?? 0}
                          </td>
                          <td style={{ padding: "0.4rem 0.5rem", textAlign: "right", whiteSpace: "nowrap" }}>
                            <button
                              className="btn btn-sm"
                              onClick={() => handleLoadSnapshot(s.id)}
                              disabled={generating || isCurrent}
                              title={isCurrent ? "Currently loaded" : "Load this snapshot"}
                              style={{ marginRight: "0.25rem" }}
                            >
                              {isCurrent ? "Loaded" : "Load"}
                            </button>
                            <button
                              className="btn btn-sm"
                              onClick={() => handleDeleteSnapshot(s.id)}
                              title="Delete this snapshot"
                              style={{ color: "#ef4444" }}
                            >
                              ✕
                            </button>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}

        {loading && !geoData && (
          <div style={{ padding: "2rem", textAlign: "center", color: "var(--text-muted)" }}>
            Loading geographic data…
          </div>
        )}

        {error && !loading && (
          <div style={{
            padding: "1rem",
            background: "rgba(217,119,6,0.12)",
            border: "1px solid rgba(217,119,6,0.35)",
            borderRadius: "8px",
            color: "var(--text)",
            marginBottom: geoData ? "1rem" : 0,
          }}>
            {error}
          </div>
        )}

        {geoData && <GeographicHeatmap data={geoData} />}
      </div>
    </main>
  );
}
