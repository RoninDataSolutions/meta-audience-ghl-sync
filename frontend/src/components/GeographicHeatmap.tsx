import { useState, useMemo } from "react";
import { geoPath, type GeoPermissibleObjects } from "d3-geo";
import { feature } from "topojson-client";
import statesTopo from "us-atlas/states-albers-10m.json";

interface StateRow {
  state: string;
  state_name: string;
  spend: number;
  impressions: number;
  clicks: number;
  contacts: number;
  paying_contacts: number;
  conversions: number;
  meta_reported_conversions: number;
  revenue: number;
  revenue_30d: number;
  total_ltv: number;
  avg_ltv: number | null;
  cpa: number | null;
  roas: number | null;
  conversion_rate_pct: number;
  classification: "wasted" | "opportunity" | "working" | null;
}

interface Narrative {
  // New unified shape
  summary?: string;
  // Old multi-field shape (kept for backward compatibility with old audit data)
  headline?: string;
  working?: string;
  wasted?: string;
  opportunity?: string;
}

interface GeoSummary {
  window_days?: number;
  total_spend: number;
  avg_monthly_spend?: number;
  total_spend_30d?: number;
  total_revenue_in_window?: number;
  total_conversions: number;
  total_conversions_30d?: number;
  total_ltv: number;
  account_avg_cpa: number | null;
  account_roas?: number | null;
  ltv_roas?: number | null;
  states_with_spend: number;
  states_with_contacts: number;
  states_with_conversions: number;
  states_with_ltv: number;
  wasted: any[];
  opportunity: any[];
  working: any[];
}

interface Props {
  data: {
    states: StateRow[];
    summary: GeoSummary;
    narrative?: Narrative;
    window_days?: number;
  };
}

function formatWindow(days: number | undefined): string {
  if (!days) return "selected window";
  if (days >= 365 && days % 365 === 0) {
    const y = days / 365;
    return y === 1 ? "1 year" : `${y} years`;
  }
  if (days >= 60 && days % 30 === 0) return `${days / 30} months`;
  return `${days} days`;
}

// Old audit data may be missing some of the newer fields — fill in safe defaults
// so the component never crashes on .toLocaleString of undefined.
function normalizeState(s: any): StateRow {
  return {
    state: s.state ?? "",
    state_name: s.state_name ?? s.state ?? "",
    spend: Number(s.spend ?? 0),
    impressions: Number(s.impressions ?? 0),
    clicks: Number(s.clicks ?? 0),
    contacts: Number(s.contacts ?? 0),
    paying_contacts: Number(s.paying_contacts ?? 0),
    conversions: Number(s.conversions ?? 0),
    meta_reported_conversions: Number(s.meta_reported_conversions ?? 0),
    revenue_30d: Number(s.revenue_30d ?? s.revenue ?? 0),
    total_ltv: Number(s.total_ltv ?? 0),
    avg_ltv: s.avg_ltv == null ? null : Number(s.avg_ltv),
    revenue: Number(s.revenue ?? s.revenue_30d ?? 0),
    cpa: s.cpa == null ? null : Number(s.cpa),
    roas: s.roas == null ? null : Number(s.roas),
    conversion_rate_pct: Number(s.conversion_rate_pct ?? 0),
    classification: s.classification ?? null,
  };
}

function normalizeSummary(s: any): GeoSummary {
  return {
    window_days: s?.window_days,
    total_spend: Number(s?.total_spend ?? s?.total_spend_30d ?? 0),
    avg_monthly_spend: s?.avg_monthly_spend == null ? undefined : Number(s.avg_monthly_spend),
    total_spend_30d: Number(s?.total_spend_30d ?? s?.total_spend ?? 0),
    total_revenue_in_window: Number(s?.total_revenue_in_window ?? 0),
    total_conversions: Number(s?.total_conversions ?? s?.total_conversions_30d ?? 0),
    total_conversions_30d: Number(s?.total_conversions_30d ?? s?.total_conversions ?? 0),
    total_ltv: Number(s?.total_ltv ?? 0),
    account_avg_cpa: s?.account_avg_cpa == null ? null : Number(s.account_avg_cpa),
    account_roas: s?.account_roas == null ? null : Number(s.account_roas),
    ltv_roas: s?.ltv_roas == null ? null : Number(s.ltv_roas),
    states_with_spend: Number(s?.states_with_spend ?? 0),
    states_with_contacts: Number(s?.states_with_contacts ?? 0),
    states_with_conversions: Number(s?.states_with_conversions ?? 0),
    states_with_ltv: Number(s?.states_with_ltv ?? 0),
    wasted: Array.isArray(s?.wasted) ? s.wasted : [],
    opportunity: Array.isArray(s?.opportunity) ? s.opportunity : [],
    working: Array.isArray(s?.working) ? s.working : [],
  };
}

type Metric = "spend" | "impressions" | "contacts" | "conversions" | "revenue_30d" | "total_ltv" | "cpa" | "conversion_rate_pct" | "roas";

const METRIC_OPTIONS: { value: Metric; label: string; format: (n: number) => string; reverse?: boolean }[] = [
  { value: "spend", label: "Spend", format: (n) => `$${n.toLocaleString(undefined, { maximumFractionDigits: 0 })}` },
  { value: "impressions", label: "Impressions", format: (n) => n.toLocaleString() },
  { value: "contacts", label: "Contacts", format: (n) => n.toLocaleString() },
  { value: "conversions", label: "Conversions (30d)", format: (n) => n.toLocaleString() },
  { value: "revenue_30d", label: "Revenue (30d)", format: (n) => `$${n.toLocaleString(undefined, { maximumFractionDigits: 0 })}` },
  { value: "total_ltv", label: "Lifetime Revenue", format: (n) => `$${n.toLocaleString(undefined, { maximumFractionDigits: 0 })}` },
  { value: "cpa", label: "CPA", format: (n) => `$${n.toLocaleString(undefined, { maximumFractionDigits: 0 })}`, reverse: true },
  { value: "roas", label: "ROAS", format: (n) => `${n.toFixed(2)}×` },
  { value: "conversion_rate_pct", label: "Conv Rate %", format: (n) => `${n.toFixed(1)}%` },
];

// US Census FIPS code → 2-letter state postal code (the us-atlas TopoJSON keys states by FIPS)
const FIPS_TO_STATE: Record<string, string> = {
  "01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA", "08": "CO", "09": "CT",
  "10": "DE", "11": "DC", "12": "FL", "13": "GA", "15": "HI", "16": "ID", "17": "IL",
  "18": "IN", "19": "IA", "20": "KS", "21": "KY", "22": "LA", "23": "ME", "24": "MD",
  "25": "MA", "26": "MI", "27": "MN", "28": "MS", "29": "MO", "30": "MT", "31": "NE",
  "32": "NV", "33": "NH", "34": "NJ", "35": "NM", "36": "NY", "37": "NC", "38": "ND",
  "39": "OH", "40": "OK", "41": "OR", "42": "PA", "44": "RI", "45": "SC", "46": "SD",
  "47": "TN", "48": "TX", "49": "UT", "50": "VT", "51": "VA", "53": "WA", "54": "WV",
  "55": "WI", "56": "WY", "72": "PR",
};

function lerpColor(c1: [number, number, number], c2: [number, number, number], t: number): string {
  const r = Math.round(c1[0] + (c2[0] - c1[0]) * t);
  const g = Math.round(c1[1] + (c2[1] - c1[1]) * t);
  const b = Math.round(c1[2] + (c2[2] - c1[2]) * t);
  return `rgb(${r}, ${g}, ${b})`;
}

function getColor(value: number, min: number, max: number, reverse = false): string {
  if (max === min) return "rgb(60, 70, 85)";
  let t = (value - min) / (max - min);
  if (reverse) t = 1 - t;
  t = Math.max(0, Math.min(1, t));
  const cold: [number, number, number] = [40, 50, 70];
  const cool: [number, number, number] = [59, 130, 246];
  const warm: [number, number, number] = [16, 185, 129];
  const hot: [number, number, number] = [245, 158, 11];
  const blazing: [number, number, number] = [239, 68, 68];
  if (t < 0.25) return lerpColor(cold, cool, t / 0.25);
  if (t < 0.5) return lerpColor(cool, warm, (t - 0.25) / 0.25);
  if (t < 0.75) return lerpColor(warm, hot, (t - 0.5) / 0.25);
  return lerpColor(hot, blazing, (t - 0.75) / 0.25);
}

// Build state features once at module load (topojson is small but processing isn't free)
const statesFeatureCollection = feature(
  statesTopo as any,
  (statesTopo as any).objects.states,
) as any;

const pathGenerator = geoPath();

export default function GeographicHeatmap({ data }: Props) {
  const [metric, setMetric] = useState<Metric>("spend");
  const [hovered, setHovered] = useState<StateRow | null>(null);

  // Normalize once — guards against older audit payloads missing new fields
  const states = useMemo(
    () => (data.states || []).map(normalizeState),
    [data.states],
  );
  const summary = useMemo(() => normalizeSummary(data.summary), [data.summary]);

  const stateMap = useMemo(() => {
    const m: Record<string, StateRow> = {};
    for (const s of states) m[s.state] = s;
    return m;
  }, [states]);

  const metricMeta = METRIC_OPTIONS.find((m) => m.value === metric)!;

  const { min, max } = useMemo(() => {
    const values = states
      .map((s) => s[metric] as number | null)
      .filter((v): v is number => typeof v === "number" && v > 0);
    if (values.length === 0) return { min: 0, max: 1 };
    return { min: Math.min(...values), max: Math.max(...values) };
  }, [states, metric]);

  // Compute label position for each state feature (centroid)
  const stateFeatures = useMemo(() => {
    return (statesFeatureCollection.features as any[]).map((f) => {
      const fips = String(f.id).padStart(2, "0");
      const code = FIPS_TO_STATE[fips];
      const d = pathGenerator(f as GeoPermissibleObjects);
      const centroid = pathGenerator.centroid(f as GeoPermissibleObjects);
      return { code, d, centroid };
    });
  }, []);

  if (states.length === 0) {
    return (
      <div style={{ padding: "1.5rem", color: "var(--text-muted)" }}>
        No geographic data available. Requires GHL contacts with state data or active US ad spend.
      </div>
    );
  }

  return (
    <div>
      {/* Narrative — single unified plain-English summary */}
      {data.narrative && (data.narrative.summary || data.narrative.headline) && (
        <div style={{
          background: "rgba(59,130,246,0.06)",
          border: "1px solid rgba(59,130,246,0.25)",
          borderRadius: "8px",
          padding: "1.25rem 1.5rem",
          marginBottom: "1.25rem",
          fontSize: "0.92rem",
          lineHeight: 1.65,
        }}>
          <div style={{
            color: "var(--primary, #3b82f6)",
            fontSize: "0.72rem",
            fontWeight: 700,
            marginBottom: "0.6rem",
            textTransform: "uppercase",
            letterSpacing: "0.05em",
          }}>
            Geographic Performance Summary
          </div>
          {data.narrative.summary ? (
            // New single-summary shape — render as flowing paragraphs
            <div style={{ whiteSpace: "pre-wrap" }}>{data.narrative.summary}</div>
          ) : (
            // Old shape fallback
            <>
              {data.narrative.headline && <div style={{ fontWeight: 600, marginBottom: "0.75rem" }}>{data.narrative.headline}</div>}
              {data.narrative.working && <div style={{ marginBottom: "0.5rem" }}>{data.narrative.working}</div>}
              {data.narrative.wasted && <div style={{ marginBottom: "0.5rem" }}>{data.narrative.wasted}</div>}
              {data.narrative.opportunity && <div>{data.narrative.opportunity}</div>}
            </>
          )}
        </div>
      )}

      {/* Metric selector */}
      <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap", marginBottom: "1rem" }}>
        {METRIC_OPTIONS.map((opt) => (
          <button
            key={opt.value}
            className="btn btn-sm"
            onClick={() => setMetric(opt.value)}
            style={{
              background: metric === opt.value ? "var(--primary, #3b82f6)" : "transparent",
              color: metric === opt.value ? "white" : "var(--text-muted)",
              borderColor: metric === opt.value ? "var(--primary, #3b82f6)" : "var(--border)",
            }}
          >
            {opt.label}
          </button>
        ))}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 280px", gap: "1rem", alignItems: "start" }}>
        {/* Map — Albers USA projection (pre-projected in us-atlas/states-albers-10m) */}
        <div style={{ background: "rgba(0,0,0,0.2)", borderRadius: "8px", padding: "1rem", position: "relative" }}>
          <svg viewBox="0 0 975 610" style={{ width: "100%", height: "auto" }}>
            {stateFeatures.map(({ code, d, centroid }) => {
              if (!d || !code) return null;
              const row = stateMap[code];
              const value = row ? ((row[metric] as number | null) ?? 0) : 0;
              const color = row && value > 0
                ? getColor(value, min, max, metricMeta.reverse)
                : "rgb(40, 50, 70)";
              return (
                <g key={code}>
                  <path
                    d={d}
                    fill={color}
                    stroke="rgba(255,255,255,0.2)"
                    strokeWidth="0.7"
                    style={{ cursor: row ? "pointer" : "default", transition: "fill 0.2s" }}
                    onMouseEnter={() => row && setHovered(row)}
                    onMouseLeave={() => setHovered(null)}
                  >
                    <title>{`${row?.state_name || code}: ${metricMeta.format(value)}`}</title>
                  </path>
                  {centroid[0] && centroid[1] && (
                    <text
                      x={centroid[0]}
                      y={centroid[1]}
                      textAnchor="middle"
                      dominantBaseline="middle"
                      fontSize="9"
                      fontWeight="600"
                      fill="rgba(255,255,255,0.7)"
                      pointerEvents="none"
                    >
                      {code}
                    </text>
                  )}
                </g>
              );
            })}
          </svg>

          {/* Legend */}
          <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginTop: "0.5rem", fontSize: "0.75rem", color: "var(--text-muted)" }}>
            <span>{metricMeta.reverse ? "high" : "low"}</span>
            <div style={{
              flex: 1,
              height: "8px",
              borderRadius: "4px",
              background: "linear-gradient(to right, rgb(40,50,70), rgb(59,130,246), rgb(16,185,129), rgb(245,158,11), rgb(239,68,68))",
            }} />
            <span>{metricMeta.reverse ? "low" : "high"}</span>
            <span style={{ marginLeft: "0.5rem" }}>
              {metricMeta.format(min)} → {metricMeta.format(max)}
            </span>
          </div>
        </div>

        {/* Side panel */}
        <div style={{ background: "rgba(0,0,0,0.2)", borderRadius: "8px", padding: "1rem", fontSize: "0.85rem" }}>
          {hovered ? (
            <>
              <div style={{ fontWeight: 700, fontSize: "1rem", marginBottom: "0.5rem" }}>
                {hovered.state_name} ({hovered.state})
              </div>
              {hovered.classification && (
                <div style={{
                  display: "inline-block",
                  background: hovered.classification === "wasted" ? "rgba(239,68,68,0.2)" : hovered.classification === "opportunity" ? "rgba(16,185,129,0.2)" : "rgba(59,130,246,0.2)",
                  color: hovered.classification === "wasted" ? "#ef4444" : hovered.classification === "opportunity" ? "#10b981" : "#3b82f6",
                  padding: "0.15rem 0.5rem",
                  borderRadius: "4px",
                  fontSize: "0.75rem",
                  fontWeight: 600,
                  textTransform: "capitalize",
                  marginBottom: "0.75rem",
                }}>
                  {hovered.classification}
                </div>
              )}
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.4rem 0.75rem", marginTop: "0.5rem" }}>
                <div><div style={{ color: "var(--text-muted)", fontSize: "0.7rem" }}>Spend (30d)</div><div>${hovered.spend.toLocaleString(undefined, { maximumFractionDigits: 0 })}</div></div>
                <div><div style={{ color: "var(--text-muted)", fontSize: "0.7rem" }}>Impressions</div><div>{hovered.impressions.toLocaleString()}</div></div>
                <div><div style={{ color: "var(--text-muted)", fontSize: "0.7rem" }}>Contacts</div><div>{hovered.contacts.toLocaleString()}</div></div>
                <div><div style={{ color: "var(--text-muted)", fontSize: "0.7rem" }}>Paying</div><div>{hovered.paying_contacts.toLocaleString()}</div></div>
                <div><div style={{ color: "var(--text-muted)", fontSize: "0.7rem" }}>Conv (30d)</div><div>{hovered.conversions.toLocaleString()}</div></div>
                <div><div style={{ color: "var(--text-muted)", fontSize: "0.7rem" }}>Rev (30d)</div><div>${hovered.revenue_30d.toLocaleString(undefined, { maximumFractionDigits: 0 })}</div></div>
                <div><div style={{ color: "var(--text-muted)", fontSize: "0.7rem" }}>Total LTV</div><div>${hovered.total_ltv.toLocaleString(undefined, { maximumFractionDigits: 0 })}</div></div>
                <div><div style={{ color: "var(--text-muted)", fontSize: "0.7rem" }}>Avg LTV</div><div>{hovered.avg_ltv ? `$${hovered.avg_ltv.toLocaleString(undefined, { maximumFractionDigits: 0 })}` : "—"}</div></div>
                <div><div style={{ color: "var(--text-muted)", fontSize: "0.7rem" }}>CPA</div><div>{hovered.cpa ? `$${hovered.cpa.toLocaleString(undefined, { maximumFractionDigits: 0 })}` : "—"}</div></div>
                <div><div style={{ color: "var(--text-muted)", fontSize: "0.7rem" }}>ROAS</div><div>{hovered.roas ? `${hovered.roas.toFixed(2)}×` : "—"}</div></div>
              </div>
            </>
          ) : (
            <>
              <div style={{ fontWeight: 600, marginBottom: "0.25rem" }}>
                Summary · {formatWindow(data.window_days ?? summary.window_days)}
              </div>
              <div style={{ fontSize: "0.78rem", marginBottom: "0.75rem", color: "var(--text-muted)" }}>
                Hover any state for details
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.5rem", marginBottom: "0.75rem" }}>
                <div><div style={{ color: "var(--text-muted)", fontSize: "0.7rem" }}>Total Spend</div><div style={{ fontWeight: 600 }}>${summary.total_spend.toLocaleString()}</div></div>
                <div><div style={{ color: "var(--text-muted)", fontSize: "0.7rem" }}>Avg / Month</div><div style={{ fontWeight: 600 }}>{summary.avg_monthly_spend ? `$${summary.avg_monthly_spend.toLocaleString(undefined, { maximumFractionDigits: 0 })}` : "—"}</div></div>
                <div><div style={{ color: "var(--text-muted)", fontSize: "0.7rem" }}>Conversions</div><div style={{ fontWeight: 600 }}>{summary.total_conversions.toLocaleString()}</div></div>
                <div><div style={{ color: "var(--text-muted)", fontSize: "0.7rem" }}>Window ROAS</div><div style={{ fontWeight: 600 }}>{summary.account_roas ? `${summary.account_roas.toFixed(2)}×` : "—"}</div></div>
                <div><div style={{ color: "var(--text-muted)", fontSize: "0.7rem" }}>Avg CPA</div><div style={{ fontWeight: 600 }}>{summary.account_avg_cpa ? `$${summary.account_avg_cpa.toLocaleString()}` : "—"}</div></div>
                <div><div style={{ color: "var(--text-muted)", fontSize: "0.7rem" }}>LTV ROAS</div><div style={{ fontWeight: 600 }}>{summary.ltv_roas ? `${summary.ltv_roas.toFixed(2)}×` : "—"}</div></div>
                <div><div style={{ color: "var(--text-muted)", fontSize: "0.7rem" }}>Total LTV</div><div style={{ fontWeight: 600 }}>${summary.total_ltv.toLocaleString()}</div></div>
              </div>

              {summary.wasted.length > 0 && (
                <div style={{ marginBottom: "0.6rem" }}>
                  <div style={{ color: "#ef4444", fontSize: "0.72rem", fontWeight: 700, marginBottom: "0.25rem" }}>WASTED</div>
                  {summary.wasted.slice(0, 3).map((w: any) => (
                    <div key={w.state} style={{ fontSize: "0.78rem" }}>{w.state_name}: ${Number(w.spend ?? 0).toLocaleString()} → {w.conversions ?? 0}c</div>
                  ))}
                </div>
              )}
              {summary.opportunity.length > 0 && (
                <div>
                  <div style={{ color: "#10b981", fontSize: "0.72rem", fontWeight: 700, marginBottom: "0.25rem" }}>OPPORTUNITY</div>
                  {summary.opportunity.slice(0, 3).map((o: any) => (
                    <div key={o.state} style={{ fontSize: "0.78rem" }}>{o.state_name}: {o.conversion_rate_pct ?? 0}% conv rate</div>
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      </div>

      {/* Full table */}
      <details style={{ marginTop: "1rem" }}>
        <summary style={{ cursor: "pointer", fontSize: "0.85rem", color: "var(--text-muted)", padding: "0.5rem" }}>
          Full state table ({states.length} states)
        </summary>
        <div style={{ marginTop: "0.5rem", maxHeight: "400px", overflowY: "auto" }}>
          <table className="history-table" style={{ fontSize: "0.8rem", width: "100%" }}>
            <thead>
              <tr>
                <th>State</th>
                <th style={{ textAlign: "right" }}>Spend</th>
                <th style={{ textAlign: "right" }}>Impr</th>
                <th style={{ textAlign: "right" }}>Contacts</th>
                <th style={{ textAlign: "right" }}>Paying</th>
                <th style={{ textAlign: "right" }}>Conv</th>
                <th style={{ textAlign: "right" }}>Rev (window)</th>
                <th style={{ textAlign: "right" }}>Total LTV</th>
                <th style={{ textAlign: "right" }}>CPA</th>
                <th>Class</th>
              </tr>
            </thead>
            <tbody>
              {states.map((s) => (
                <tr key={s.state}>
                  <td>{s.state_name} ({s.state})</td>
                  <td style={{ textAlign: "right" }}>${s.spend.toLocaleString(undefined, { maximumFractionDigits: 0 })}</td>
                  <td style={{ textAlign: "right" }}>{s.impressions.toLocaleString()}</td>
                  <td style={{ textAlign: "right" }}>{s.contacts}</td>
                  <td style={{ textAlign: "right" }}>{s.paying_contacts}</td>
                  <td style={{ textAlign: "right" }}>{s.conversions}</td>
                  <td style={{ textAlign: "right" }}>${s.revenue_30d.toLocaleString(undefined, { maximumFractionDigits: 0 })}</td>
                  <td style={{ textAlign: "right" }}>${s.total_ltv.toLocaleString(undefined, { maximumFractionDigits: 0 })}</td>
                  <td style={{ textAlign: "right" }}>{s.cpa ? `$${s.cpa.toLocaleString(undefined, { maximumFractionDigits: 0 })}` : "—"}</td>
                  <td>
                    {s.classification && (
                      <span style={{
                        background: s.classification === "wasted" ? "rgba(239,68,68,0.2)" : s.classification === "opportunity" ? "rgba(16,185,129,0.2)" : "rgba(59,130,246,0.2)",
                        color: s.classification === "wasted" ? "#ef4444" : s.classification === "opportunity" ? "#10b981" : "#3b82f6",
                        padding: "0.1rem 0.4rem",
                        borderRadius: "3px",
                        fontSize: "0.7rem",
                        textTransform: "capitalize",
                      }}>
                        {s.classification}
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}
