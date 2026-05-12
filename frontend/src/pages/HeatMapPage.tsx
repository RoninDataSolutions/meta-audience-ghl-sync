import { useState, useEffect } from "react";
import type { AdAccount } from "../types";
import { getAuditReports, getAuditReport, generateHeatmap } from "../api";
import GeographicHeatmap from "../components/GeographicHeatmap";

interface Props {
  selectedAccount: AdAccount | null;
}

interface DataSource {
  type: "audit" | "fresh";
  generatedAt: string | null;
  reportId?: number;
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
      setSource({ type: "fresh", generatedAt: result.generated_at, days: result.days });
    } catch (e: any) {
      setError(e.message || "Heat map generation failed");
    } finally {
      setGenerating(false);
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
                  : `Freshly generated · ${windowLabel(source.days)} window · ${source.generatedAt ? new Date(source.generatedAt).toLocaleString() : "—"}`}
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
          <button
            className="btn btn-primary"
            onClick={() => handleGenerate()}
            disabled={generating}
            title="Refresh the heat map with current window (~10-20s)"
          >
            {generating ? "Generating…" : "↻ Generate"}
          </button>
        </div>

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
