import { useState, useMemo, useEffect } from "react";
import { geoPath, type GeoPermissibleObjects } from "d3-geo";
import { feature } from "topojson-client";
import statesTopo from "us-atlas/states-albers-10m.json";

// ── Types ────────────────────────────────────────────────────────────────────

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
  classification: "high_roas" | "medium_roas" | "low_roas" | "no_roas" | "untapped" | null;
}

interface Narrative {
  summary?: string;
  inclusion_states?: string[];
  inclusion_state_names?: string[];
  inclusion_csv?: string;
  exclusion_states?: string[];
  exclusion_state_names?: string[];
  exclusion_csv?: string;
  tier_totals?: {
    high_spend: number;
    high_ltv: number;
    high_count: number;
    medium_count: number;
    low_spend: number;
    low_count: number;
    no_roas_spend: number;
    no_roas_count: number;
    untapped_count: number;
  };
  reallocation?: {
    recoverable_spend: number;
    upside_avg_roas: number;
    projected_revenue_gain: number;
  };
  // Legacy fields
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

// ── Helpers ──────────────────────────────────────────────────────────────────

function formatWindow(days: number | undefined): string {
  if (!days) return "selected window";
  if (days >= 365 && days % 365 === 0) {
    const y = days / 365;
    return y === 1 ? "1 year" : `${y} years`;
  }
  if (days >= 60 && days % 30 === 0) return `${days / 30} months`;
  return `${days} days`;
}

function shortWindow(days: number | undefined): string {
  if (!days) return "window";
  if (days >= 365 && days % 365 === 0) return `${days / 365}y`;
  if (days >= 60 && days % 30 === 0) return `${days / 30}mo`;
  return `${days}d`;
}

const fmtDollars = (n: number) => `$${Math.round(n).toLocaleString()}`;

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

// ── Map paths ────────────────────────────────────────────────────────────────

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

const statesFeatureCollection = feature(
  statesTopo as any,
  (statesTopo as any).objects.states,
) as any;
const pathGenerator = geoPath();

// ── Color helpers ────────────────────────────────────────────────────────────

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

// ── Tier styling ─────────────────────────────────────────────────────────────

const TIER_STYLE = {
  high_roas:   { color: "#34d399", icon: "▲", label: "High ROAS",   subtitle: "Scale 15–25%" },
  medium_roas: { color: "#fbbf24", icon: "—", label: "Medium ROAS", subtitle: "Hold steady" },
  low_roas:    { color: "#fb7185", icon: "▼", label: "Low ROAS",    subtitle: "Cut — losing money" },
  no_roas:     { color: "#64748b", icon: "✗", label: "Zero ROAS",   subtitle: "Exclude — no return" },
  untapped:    { color: "#818cf8", icon: "◇", label: "Untapped",    subtitle: "Prospecting opportunity" },
};

// ── Sub-components ───────────────────────────────────────────────────────────

function RoasBar({ spent, ltv, maxLtv }: { spent: number; ltv: number; maxLtv: number }) {
  const spentW = maxLtv > 0 ? Math.min((spent / maxLtv) * 100, 100) : 0;
  const ltvW = maxLtv > 0 ? Math.min((ltv / maxLtv) * 100, 100) : 0;
  return (
    <div style={{ position: "relative", height: 6, background: "rgba(255,255,255,0.04)", borderRadius: 3, overflow: "hidden" }}>
      <div style={{ position: "absolute", left: 0, top: 0, height: "100%", width: `${ltvW}%`, background: "rgba(52,211,153,0.25)", borderRadius: 3 }} />
      <div style={{ position: "absolute", left: 0, top: 0, height: "100%", width: `${spentW}%`, background: "rgba(251,113,133,0.5)", borderRadius: 3 }} />
    </div>
  );
}

function CopyBlock({ label, text, accent }: { label: string; text: string; accent: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch { /* ignored */ }
  };
  return (
    <div style={{
      background: "rgba(255,255,255,0.03)",
      border: "1px solid rgba(255,255,255,0.06)",
      borderRadius: 10,
      padding: "16px 20px",
      marginTop: 12,
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
        <span style={{
          fontFamily: "'DM Sans', sans-serif",
          fontSize: 11, fontWeight: 600,
          letterSpacing: "0.08em", textTransform: "uppercase",
          color: accent,
        }}>
          {label}
        </span>
        <button
          onClick={copy}
          className="no-print"
          style={{
            background: copied ? "rgba(52,211,153,0.15)" : "rgba(255,255,255,0.06)",
            border: "1px solid rgba(255,255,255,0.08)",
            borderRadius: 6, padding: "4px 12px", cursor: "pointer",
            fontFamily: "'DM Sans', sans-serif", fontSize: 11, fontWeight: 500,
            color: copied ? "#34d399" : "rgba(255,255,255,0.5)",
            transition: "all 0.2s",
          }}
        >
          {copied ? "✓ Copied" : "Copy"}
        </button>
      </div>
      <p style={{
        fontFamily: "'DM Mono', monospace",
        fontSize: 12,
        color: "rgba(255,255,255,0.55)",
        lineHeight: 1.6, margin: 0,
        wordBreak: "break-word",
      }}>
        {text}
      </p>
      <p style={{
        fontFamily: "'DM Sans', sans-serif",
        fontSize: 10.5,
        color: "rgba(255,255,255,0.25)",
        margin: "8px 0 0", fontStyle: "italic",
      }}>
        Paste into Ad Set → Audience → Locations → {label.includes("Include") ? "Include" : "Exclude"}
      </p>
    </div>
  );
}

function TierSection({
  tier, rows, maxLtv, summary,
}: {
  tier: keyof typeof TIER_STYLE;
  rows: StateRow[];
  maxLtv: number;
  summary?: string;
}) {
  if (rows.length === 0) return null;
  const style = TIER_STYLE[tier];
  const fmt = (n: number) => `$${Math.round(n).toLocaleString()}`;
  const showCustomers = tier === "high_roas";

  return (
    <div style={{
      background: "rgba(255,255,255,0.02)",
      border: "1px solid rgba(255,255,255,0.05)",
      borderRadius: 12,
      padding: "20px 24px",
      marginBottom: 14,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 4 }}>
        <span style={{ color: style.color, fontSize: 14, fontWeight: 700 }}>{style.icon}</span>
        <h2 style={{ fontSize: 16, fontWeight: 700, margin: 0, fontFamily: "'DM Sans', sans-serif" }}>{style.label}</h2>
        <span style={{ fontSize: 11, color: "rgba(255,255,255,0.35)", fontWeight: 500, marginLeft: "auto" }}>{style.subtitle}</span>
      </div>
      {summary && (
        <p style={{ fontSize: 11.5, color: "rgba(255,255,255,0.4)", margin: "4px 0 14px", fontFamily: "'DM Mono', monospace" }}>
          {summary}
        </p>
      )}

      {tier === "untapped" ? (
        <div>
          {rows.map((r) => (
            <div key={r.state} style={{ display: "flex", alignItems: "center", gap: 16, padding: "8px 0" }}>
              <span style={{ fontSize: 14, fontWeight: 600 }}>
                <span style={{ color: "rgba(255,255,255,0.35)", fontSize: 11, marginRight: 6, fontFamily: "'DM Mono', monospace" }}>{r.state}</span>
                {r.state_name}
              </span>
              <span style={{ fontSize: 12, color: style.color, fontFamily: "'DM Mono', monospace", fontWeight: 600 }}>{fmt(r.total_ltv)} LTV</span>
              <span style={{ fontSize: 11, color: "rgba(255,255,255,0.3)" }}>only {fmt(r.spend)} spent</span>
            </div>
          ))}
          <p style={{ fontSize: 12, color: "rgba(255,255,255,0.4)", margin: "8px 0 0", lineHeight: 1.5 }}>
            → Build a state-targeted prospecting campaign + Lookalike 1% from top-LTV customers
          </p>
        </div>
      ) : tier === "no_roas" ? (
        <p style={{ fontSize: 12, color: "rgba(255,255,255,0.4)", lineHeight: 1.8, margin: 0, fontFamily: "'DM Mono', monospace" }}>
          {rows.map((r) => r.state_name).join(", ")}
        </p>
      ) : (
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr style={{ borderBottom: "1px solid rgba(255,255,255,0.06)" }}>
              {["State", "Spent", "LTV", "ROAS", ...(showCustomers ? ["Cust."] : []), ""].map((h, i, arr) => (
                <th key={i} style={{
                  padding: "8px 8px 8px 0", fontSize: 10, fontWeight: 600,
                  letterSpacing: "0.06em", textTransform: "uppercase",
                  color: "rgba(255,255,255,0.3)",
                  textAlign: i > 0 && i < arr.length - 1 ? "right" : "left",
                  fontFamily: "'DM Sans', sans-serif",
                }}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const roas = r.spend > 0 ? Math.max(r.revenue_30d, r.total_ltv) / r.spend : 0;
              return (
                <tr key={r.state} style={{ borderBottom: "1px solid rgba(255,255,255,0.03)" }}>
                  <td style={{ padding: "10px 8px 10px 0", fontSize: 13, fontWeight: 500, fontFamily: "'DM Sans', sans-serif" }}>
                    <span style={{ color: "rgba(255,255,255,0.35)", fontSize: 11, marginRight: 6, fontFamily: "'DM Mono', monospace" }}>{r.state}</span>
                    {r.state_name}
                  </td>
                  <td style={{ textAlign: "right", fontSize: 12, color: "rgba(255,255,255,0.5)", fontFamily: "'DM Mono', monospace" }}>{fmt(r.spend)}</td>
                  <td style={{ textAlign: "right", fontSize: 12, fontWeight: 600, color: style.color, fontFamily: "'DM Mono', monospace" }}>{fmt(r.total_ltv)}</td>
                  <td style={{ textAlign: "right", fontSize: 12, fontWeight: 600, fontFamily: "'DM Mono', monospace" }}>{roas.toFixed(roas < 1 ? 2 : 1)}×</td>
                  {showCustomers && (
                    <td style={{ textAlign: "right", fontSize: 12, color: "rgba(255,255,255,0.4)", fontFamily: "'DM Mono', monospace" }}>
                      {r.paying_contacts || "—"}
                    </td>
                  )}
                  <td style={{ width: 120, padding: "10px 0 10px 12px" }}>
                    <RoasBar spent={r.spend} ltv={r.total_ltv} maxLtv={maxLtv} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}

function MapView({ states, data }: { states: StateRow[]; data: Props["data"] }) {
  type Metric = "spend" | "impressions" | "contacts" | "conversions" | "revenue_30d" | "total_ltv" | "cpa" | "roas" | "conversion_rate_pct";
  const [metric, setMetric] = useState<Metric>("spend");
  const [hovered, setHovered] = useState<StateRow | null>(null);

  const windowDays = data.window_days;
  const windowShort = shortWindow(windowDays);

  const metricOptions: { value: Metric; label: string; format: (n: number) => string; reverse?: boolean }[] = useMemo(() => [
    { value: "spend", label: "Spend", format: (n) => fmtDollars(n) },
    { value: "impressions", label: "Impressions", format: (n) => n.toLocaleString() },
    { value: "contacts", label: "Contacts", format: (n) => n.toLocaleString() },
    { value: "conversions", label: `Conversions (${windowShort})`, format: (n) => n.toLocaleString() },
    { value: "revenue_30d", label: `Revenue (${windowShort})`, format: (n) => fmtDollars(n) },
    { value: "total_ltv", label: "Lifetime Revenue", format: (n) => fmtDollars(n) },
    { value: "cpa", label: "CPA", format: (n) => fmtDollars(n), reverse: true },
    { value: "roas", label: "ROAS", format: (n) => `${n.toFixed(2)}×` },
    { value: "conversion_rate_pct", label: "Conv Rate %", format: (n) => `${n.toFixed(1)}%` },
  ], [windowShort]);

  const metricMeta = metricOptions.find((m) => m.value === metric)!;
  const stateMap = useMemo(() => Object.fromEntries(states.map((s) => [s.state, s])), [states]);

  const { min, max } = useMemo(() => {
    const values = states.map((s) => s[metric] as number | null).filter((v): v is number => typeof v === "number" && v > 0);
    if (values.length === 0) return { min: 0, max: 1 };
    return { min: Math.min(...values), max: Math.max(...values) };
  }, [states, metric]);

  const stateFeatures = useMemo(() => {
    return (statesFeatureCollection.features as any[]).map((f) => {
      const fips = String(f.id).padStart(2, "0");
      return {
        code: FIPS_TO_STATE[fips],
        d: pathGenerator(f as GeoPermissibleObjects),
        centroid: pathGenerator.centroid(f as GeoPermissibleObjects),
      };
    });
  }, []);

  return (
    <div>
      <div className="no-print" style={{ display: "flex", gap: "0.4rem", flexWrap: "wrap", marginBottom: "0.85rem" }}>
        {metricOptions.map((opt) => (
          <button
            key={opt.value}
            onClick={() => setMetric(opt.value)}
            style={{
              padding: "4px 10px",
              fontSize: 11, fontWeight: 600,
              fontFamily: "'DM Sans', sans-serif",
              borderRadius: 6, cursor: "pointer",
              border: `1px solid ${metric === opt.value ? "rgba(52,211,153,0.4)" : "rgba(255,255,255,0.08)"}`,
              background: metric === opt.value ? "rgba(52,211,153,0.12)" : "rgba(255,255,255,0.03)",
              color: metric === opt.value ? "#34d399" : "rgba(255,255,255,0.55)",
              transition: "all 0.15s",
            }}
          >
            {opt.label}
          </button>
        ))}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 280px", gap: "1rem", alignItems: "start" }}>
        <div style={{ background: "rgba(255,255,255,0.02)", border: "1px solid rgba(255,255,255,0.05)", borderRadius: 10, padding: "1rem" }}>
          <svg viewBox="0 0 975 610" style={{ width: "100%", height: "auto" }}>
            {stateFeatures.map(({ code, d, centroid }) => {
              if (!d || !code) return null;
              const row = stateMap[code];
              const value = row ? ((row[metric] as number | null) ?? 0) : 0;
              const color = row && value > 0 ? getColor(value, min, max, metricMeta.reverse) : "rgb(40,50,70)";
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
                    <text x={centroid[0]} y={centroid[1]} textAnchor="middle" dominantBaseline="middle"
                          fontSize="9" fontWeight="600" fill="rgba(255,255,255,0.7)" pointerEvents="none">
                      {code}
                    </text>
                  )}
                </g>
              );
            })}
          </svg>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 8, fontSize: 11, color: "rgba(255,255,255,0.4)", fontFamily: "'DM Sans', sans-serif" }}>
            <span>{metricMeta.reverse ? "high" : "low"}</span>
            <div style={{ flex: 1, height: 6, borderRadius: 3, background: "linear-gradient(to right, rgb(40,50,70), rgb(59,130,246), rgb(16,185,129), rgb(245,158,11), rgb(239,68,68))" }} />
            <span>{metricMeta.reverse ? "low" : "high"}</span>
            <span style={{ marginLeft: 8, fontFamily: "'DM Mono', monospace" }}>
              {metricMeta.format(min)} → {metricMeta.format(max)}
            </span>
          </div>
        </div>

        <div style={{ background: "rgba(255,255,255,0.02)", border: "1px solid rgba(255,255,255,0.05)", borderRadius: 10, padding: "1rem", fontFamily: "'DM Sans', sans-serif" }}>
          {hovered ? (
            <>
              <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 8 }}>
                {hovered.state_name}
                <span style={{ marginLeft: 6, fontSize: 11, color: "rgba(255,255,255,0.35)", fontFamily: "'DM Mono', monospace" }}>{hovered.state}</span>
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.5rem 1rem", fontSize: 12 }}>
                <Stat label={`Spend (${windowShort})`} v={fmtDollars(hovered.spend)} />
                <Stat label="Impressions" v={hovered.impressions.toLocaleString()} />
                <Stat label="Contacts" v={hovered.contacts.toLocaleString()} />
                <Stat label="Paying" v={hovered.paying_contacts.toLocaleString()} />
                <Stat label={`Conv (${windowShort})`} v={hovered.conversions.toLocaleString()} />
                <Stat label={`Rev (${windowShort})`} v={fmtDollars(hovered.revenue_30d)} />
                <Stat label="Total LTV" v={fmtDollars(hovered.total_ltv)} />
                <Stat label="Avg LTV" v={hovered.avg_ltv ? fmtDollars(hovered.avg_ltv) : "—"} />
                <Stat label="CPA" v={hovered.cpa ? fmtDollars(hovered.cpa) : "—"} />
                <Stat label="ROAS" v={hovered.roas ? `${hovered.roas.toFixed(2)}×` : "—"} />
              </div>
            </>
          ) : (
            <div style={{ fontSize: 12, color: "rgba(255,255,255,0.45)" }}>
              Hover any state on the map to see its breakdown.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function Stat({ label, v }: { label: string; v: string | number }) {
  return (
    <div>
      <div style={{ color: "rgba(255,255,255,0.35)", fontSize: 10, fontWeight: 600, letterSpacing: "0.04em", textTransform: "uppercase" }}>{label}</div>
      <div style={{ fontFamily: "'DM Mono', monospace" }}>{v}</div>
    </div>
  );
}

// ── Main component ──────────────────────────────────────────────────────────

export default function GeographicHeatmap({ data }: Props) {
  const [tab, setTab] = useState<"map" | "tiers" | "actions">("tiers");

  const states = useMemo(() => (data.states || []).map(normalizeState), [data.states]);
  const summary = useMemo(() => normalizeSummary(data.summary), [data.summary]);
  const narrative = data.narrative;
  const reall = narrative?.reallocation;
  const tierTotals = narrative?.tier_totals;

  const byTier = useMemo(() => {
    const groups: Record<string, StateRow[]> = {
      high_roas: [], medium_roas: [], low_roas: [], no_roas: [], untapped: [],
    };
    for (const s of states) {
      if (s.classification && groups[s.classification]) {
        groups[s.classification].push(s);
      }
    }
    // Sort each tier by spend desc (or LTV for untapped)
    groups.high_roas.sort((a, b) => (Math.max(b.revenue_30d, b.total_ltv) / Math.max(b.spend, 1)) - (Math.max(a.revenue_30d, a.total_ltv) / Math.max(a.spend, 1)));
    groups.medium_roas.sort((a, b) => b.total_ltv - a.total_ltv);
    groups.low_roas.sort((a, b) => b.spend - a.spend);
    groups.no_roas.sort((a, b) => b.spend - a.spend);
    groups.untapped.sort((a, b) => b.total_ltv - a.total_ltv);
    return groups;
  }, [states]);

  const maxLtv = useMemo(() => Math.max(1, ...states.map((s) => s.total_ltv)), [states]);

  // Compute tier summaries
  const highRevenue = byTier.high_roas.reduce((sum, r) => sum + Math.max(r.revenue_30d, r.total_ltv), 0);
  const highSpend = byTier.high_roas.reduce((sum, r) => sum + r.spend, 0);
  const highAvgRoas = highSpend > 0 ? highRevenue / highSpend : 0;

  const medRevenue = byTier.medium_roas.reduce((sum, r) => sum + Math.max(r.revenue_30d, r.total_ltv), 0);
  const medSpend = byTier.medium_roas.reduce((sum, r) => sum + r.spend, 0);
  const medAvgRoas = medSpend > 0 ? medRevenue / medSpend : 0;

  const lowSpend = byTier.low_roas.reduce((sum, r) => sum + r.spend, 0);
  const lowRevenue = byTier.low_roas.reduce((sum, r) => sum + Math.max(r.revenue_30d, r.total_ltv), 0);
  const lowLoss = lowSpend - lowRevenue;

  const zeroSpend = byTier.no_roas.reduce((sum, r) => sum + r.spend, 0);

  if (states.length === 0) {
    return (
      <div style={{ padding: "1.5rem", color: "rgba(255,255,255,0.5)" }}>
        No geographic data available. Requires GHL contacts with state data or active US ad spend.
      </div>
    );
  }

  return (
    <div className="heatmap-dashboard" style={{ fontFamily: "'DM Sans', sans-serif", color: "#e8e8ec" }}>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "baseline", gap: 12, marginBottom: 4 }}>
        <h1 style={{ fontFamily: "'Playfair Display', serif", fontSize: 28, fontWeight: 800, margin: 0, letterSpacing: "-0.02em", color: "#e8e8ec" }}>
          Geo ROAS
        </h1>
        <span style={{ fontSize: 12, color: "rgba(255,255,255,0.3)", fontWeight: 500, letterSpacing: "0.06em", textTransform: "uppercase" }}>
          Last {formatWindow(data.window_days ?? summary.window_days)}
        </span>
      </div>
      <p style={{ fontSize: 13.5, color: "rgba(255,255,255,0.4)", margin: "4px 0 20px", lineHeight: 1.5 }}>
        Geographic ad performance by lifetime value return on ad spend
      </p>

      {/* Summary cards */}
      <div className="heatmap-summary-cards" style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 10, marginBottom: 24 }}>
        <SummaryCard label="Total Spend" value={fmtDollars(summary.total_spend)} sub={summary.avg_monthly_spend ? `~${fmtDollars(summary.avg_monthly_spend)}/mo` : ""} />
        <SummaryCard label="Lifetime Value" value={fmtDollars(summary.total_ltv)} sub="from paying geos" />
        <SummaryCard label="Blended ROAS" value={summary.ltv_roas ? `${summary.ltv_roas.toFixed(2)}×` : summary.account_roas ? `${summary.account_roas.toFixed(2)}×` : "—"} sub="overall return" />
        <SummaryCard
          label="Projected Gain"
          value={reall && reall.projected_revenue_gain > 0 ? `+${fmtDollars(reall.projected_revenue_gain)}` : "—"}
          sub={reall && reall.recoverable_spend > 0 ? `from ${fmtDollars(reall.recoverable_spend)} reallocated` : "after reallocation"}
          accent
        />
      </div>

      {/* Tabs */}
      <div className="heatmap-tabs no-print" style={{ display: "flex", gap: 2, marginBottom: 18, background: "rgba(255,255,255,0.03)", borderRadius: 8, padding: 3 }}>
        {[
          { id: "map" as const, label: "Heat Map" },
          { id: "tiers" as const, label: "Performance Tiers" },
          { id: "actions" as const, label: "Action Items" },
        ].map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            style={{
              flex: 1, padding: "8px 0", border: "none", borderRadius: 6, cursor: "pointer",
              fontFamily: "'DM Sans', sans-serif", fontSize: 12, fontWeight: 600,
              background: tab === t.id ? "rgba(255,255,255,0.08)" : "transparent",
              color: tab === t.id ? "#e8e8ec" : "rgba(255,255,255,0.4)",
              transition: "all 0.2s",
            }}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* MAP (always rendered, hidden when not active, shown when printing) */}
      <section className="print-section" style={{ display: tab === "map" ? "block" : "none" }}>
        <MapView states={states} data={data} />
      </section>

      {/* TIERS */}
      <section className="print-section" style={{ display: tab === "tiers" ? "block" : "none" }}>
        <TierSection
          tier="high_roas" rows={byTier.high_roas} maxLtv={maxLtv}
          summary={byTier.high_roas.length ? `${fmtDollars(highRevenue)} LTV on ${fmtDollars(highSpend)} spent · Avg ${highAvgRoas.toFixed(1)}× ROAS` : undefined}
        />
        <TierSection
          tier="medium_roas" rows={byTier.medium_roas} maxLtv={maxLtv}
          summary={byTier.medium_roas.length ? `${fmtDollars(medRevenue)} LTV on ${fmtDollars(medSpend)} spent · Avg ${medAvgRoas.toFixed(1)}× ROAS` : undefined}
        />
        <TierSection
          tier="low_roas" rows={byTier.low_roas} maxLtv={maxLtv}
          summary={byTier.low_roas.length ? `${fmtDollars(lowRevenue)} LTV on ${fmtDollars(lowSpend)} spent · Losing ${fmtDollars(lowLoss)} below 1× return` : undefined}
        />
        <TierSection
          tier="no_roas" rows={byTier.no_roas} maxLtv={maxLtv}
          summary={byTier.no_roas.length ? `${byTier.no_roas.length} states · ${fmtDollars(zeroSpend)} spent · No paying customers` : undefined}
        />
        <TierSection
          tier="untapped" rows={byTier.untapped} maxLtv={maxLtv}
          summary={byTier.untapped.length ? "Existing customers — minimal ad spend" : undefined}
        />
      </section>

      {/* ACTIONS */}
      <section className="print-section" style={{ display: tab === "actions" ? "block" : "none" }}>
        {reall && reall.recoverable_spend > 0 && reall.projected_revenue_gain > 0 && (
          <div style={{
            background: "linear-gradient(135deg, rgba(52,211,153,0.08), rgba(129,140,248,0.06))",
            border: "1px solid rgba(52,211,153,0.18)",
            borderRadius: 12, padding: "20px 24px", marginBottom: 22,
          }}>
            <div style={{ fontSize: 11, fontWeight: 600, letterSpacing: "0.08em", textTransform: "uppercase", color: "#34d399", marginBottom: 8 }}>
              ⇄ Reallocation Opportunity
            </div>
            <p style={{ fontSize: 14, color: "rgba(255,255,255,0.82)", margin: 0, lineHeight: 1.6 }}>
              Cutting <strong style={{ color: "#fb7185" }}>{fmtDollars(reall.recoverable_spend)}</strong> of Low + Zero ROAS spend and redirecting to
              High ROAS geos (avg {reall.upside_avg_roas.toFixed(1)}×) projects <strong style={{ color: "#34d399" }}>~{fmtDollars(reall.projected_revenue_gain)}</strong> in additional revenue.
            </p>
          </div>
        )}

        <div style={{ display: "flex", flexDirection: "column", gap: 14, marginBottom: 22 }}>
          {[
            tierTotals && tierTotals.high_count > 0
              ? { num: "01", text: `Scale High ROAS — these ${tierTotals.high_count} states return ${highAvgRoas.toFixed(1)}× on spend. Lift daily budgets 15-25%.`, color: "#34d399" }
              : null,
            tierTotals && tierTotals.medium_count > 0
              ? { num: "02", text: `Hold Medium ROAS — ${tierTotals.medium_count} states profitable but not yet ready to scale.`, color: "#fbbf24" }
              : null,
            tierTotals && (tierTotals.low_count > 0 || tierTotals.no_roas_count > 0)
              ? { num: "03", text: `Exclude Low + Zero ROAS — ${(tierTotals.low_count + tierTotals.no_roas_count)} states wasting ${fmtDollars(tierTotals.low_spend + tierTotals.no_roas_spend)}.`, color: "#fb7185" }
              : null,
            tierTotals && tierTotals.untapped_count > 0
              ? { num: "04", text: `Launch prospecting in ${tierTotals.untapped_count} untapped state(s) — state-targeted campaign + Lookalike 1% audience.`, color: "#818cf8" }
              : null,
          ].filter(Boolean).map((s: any) => (
            <div key={s.num} style={{ display: "flex", gap: 14, alignItems: "flex-start" }}>
              <span style={{ fontFamily: "'DM Mono', monospace", fontSize: 13, fontWeight: 600, color: s.color, minWidth: 24 }}>{s.num}</span>
              <span style={{ fontSize: 13, color: "rgba(255,255,255,0.75)", lineHeight: 1.5 }}>{s.text}</span>
            </div>
          ))}
        </div>

        {narrative?.inclusion_csv && (
          <CopyBlock
            label={`✓ Include in ads — ${narrative.inclusion_state_names?.length ?? 0} states`}
            text={narrative.inclusion_csv}
            accent="#34d399"
          />
        )}
        {narrative?.exclusion_csv && (
          <CopyBlock
            label={`✗ Exclude from ads — ${narrative.exclusion_state_names?.length ?? 0} states`}
            text={narrative.exclusion_csv}
            accent="#fb7185"
          />
        )}
      </section>

      {/* PRINT-ONLY: render all sections sequentially when printing */}
      <PrintStyles />
    </div>
  );
}

function SummaryCard({ label, value, sub, accent }: { label: string; value: string; sub: string; accent?: boolean }) {
  return (
    <div style={{
      background: accent ? "rgba(52,211,153,0.06)" : "rgba(255,255,255,0.03)",
      border: `1px solid ${accent ? "rgba(52,211,153,0.18)" : "rgba(255,255,255,0.06)"}`,
      borderRadius: 10, padding: "14px 16px",
    }}>
      <div style={{ fontSize: 10, fontWeight: 600, letterSpacing: "0.08em", textTransform: "uppercase", color: "rgba(255,255,255,0.4)", marginBottom: 6, fontFamily: "'DM Sans', sans-serif" }}>{label}</div>
      <div style={{ fontSize: 20, fontWeight: 700, color: accent ? "#34d399" : "#e8e8ec", fontFamily: "'DM Mono', monospace" }}>{value}</div>
      {sub && <div style={{ fontSize: 11, color: "rgba(255,255,255,0.35)", marginTop: 2, fontFamily: "'DM Sans', sans-serif" }}>{sub}</div>}
    </div>
  );
}

// Print styles — show all sections sequentially, hide controls, white background
function PrintStyles() {
  useEffect(() => {
    if (document.getElementById("heatmap-print-styles")) return;
    const style = document.createElement("style");
    style.id = "heatmap-print-styles";
    style.textContent = `
      @media print {
        @page { size: A4; margin: 16mm 14mm; }
        body { background: #0a0a0c !important; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
        nav, .tab-nav, .Header, header.app-header,
        .no-print, .heatmap-tabs { display: none !important; }
        .heatmap-dashboard { background: #0a0a0c; color: #e8e8ec; padding: 0 !important; }
        .heatmap-dashboard .print-section { display: block !important; page-break-before: always; }
        .heatmap-dashboard .print-section:first-of-type { page-break-before: avoid; }
        .heatmap-summary-cards { page-break-after: avoid; }
        .card { background: transparent !important; border: none !important; box-shadow: none !important; padding: 0 !important; }
      }
    `;
    document.head.appendChild(style);
    return () => { /* keep styles installed */ };
  }, []);
  return null;
}
