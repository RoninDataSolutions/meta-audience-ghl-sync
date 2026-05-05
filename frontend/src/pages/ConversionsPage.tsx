import { useState, useEffect, useCallback } from "react";
import type { AdAccount, Conversion, ConversionStats } from "../types";
import { getConversions, getConversionDetail, retryConversion } from "../api";

function fmtDate(s: string | null) {
  if (!s) return "—";
  return new Date(s).toLocaleString();
}

function fmtMoney(n: number, currency = "usd") {
  return n.toLocaleString(undefined, {
    style: "currency",
    currency: currency.toUpperCase(),
    minimumFractionDigits: 2,
  });
}

const STATUS_COLORS: Record<string, string> = {
  sent:    "var(--success)",
  failed:  "var(--danger)",
  pending: "var(--warning)",
  skipped: "var(--text-muted)",
};

function StatusBadge({ status }: { status: string }) {
  return (
    <span style={{
      display: "inline-block",
      padding: "0.15rem 0.5rem",
      borderRadius: "9999px",
      fontSize: "0.72rem",
      fontWeight: 700,
      textTransform: "uppercase",
      background: `color-mix(in srgb, ${STATUS_COLORS[status] ?? "var(--text-muted)"} 15%, transparent)`,
      color: STATUS_COLORS[status] ?? "var(--text-muted)",
    }}>
      {status}
    </span>
  );
}

function StatCard({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div style={{
      background: "var(--surface)",
      border: "1px solid var(--border)",
      borderRadius: "0.5rem",
      padding: "1rem 1.25rem",
      flex: 1,
      minWidth: "120px",
    }}>
      <div style={{ fontSize: "0.72rem", color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: "0.35rem" }}>
        {label}
      </div>
      <div style={{ fontSize: "1.4rem", fontWeight: 700 }}>{value}</div>
      {sub && <div style={{ fontSize: "0.75rem", color: "var(--text-muted)", marginTop: "0.15rem" }}>{sub}</div>}
    </div>
  );
}

function ConversionDetailModal({ id, onClose }: { id: number; onClose: () => void }) {
  const [detail, setDetail] = useState<Conversion | null>(null);
  const [retrying, setRetrying] = useState(false);
  const [retryMsg, setRetryMsg] = useState("");

  useEffect(() => {
    getConversionDetail(id).then(setDetail).catch(() => {});
  }, [id]);

  const handleRetry = async () => {
    if (!detail) return;
    setRetrying(true);
    setRetryMsg("");
    try {
      const res = await retryConversion(id);
      setRetryMsg(res.status === "sent" ? "Sent successfully" : res.status);
      const refreshed = await getConversionDetail(id);
      setDetail(refreshed);
    } catch (e: any) {
      setRetryMsg(`Failed: ${e.message}`);
    } finally {
      setRetrying(false);
    }
  };

  if (!detail) return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()} style={{ maxWidth: "560px" }}>
        <div style={{ padding: "2rem", color: "var(--text-muted)", textAlign: "center" }}>Loading…</div>
      </div>
    </div>
  );

  const rows: [string, string][] = [
    ["Stripe Session", detail.stripe_session_id],
    ["Customer",       [detail.stripe_name, detail.stripe_email].filter(Boolean).join(" · ") || "—"],
    ["Amount",         fmtMoney(detail.amount, detail.currency)],
    ["Source",         detail.source],
    ["Created",        fmtDate(detail.created_at)],
    ["GHL Contact",    detail.ghl_contact_id ?? "—"],
    ["GHL Name",       detail.ghl_name ?? "—"],
    ["GHL Email",      detail.ghl_email ?? "—"],
    ["GHL Phone",      detail.ghl_phone ?? "—"],
    ["Match Method",   detail.match_method],
    ["Match Score",    detail.match_score != null ? String(detail.match_score) : "—"],
    ["fbclid",         detail.ghl_fbclid ? "yes" : "no"],
    ["fbp",            detail.ghl_fbp ?? "—"],
    ["UTM Source",     detail.ghl_utm_source ?? "—"],
    ["UTM Campaign",   detail.ghl_utm_campaign ?? "—"],
    ["CAPI Event ID",  detail.capi_event_id ?? "—"],
    ["CAPI Status",    detail.capi_status],
    ["CAPI Sent At",   fmtDate(detail.capi_sent_at)],
  ];

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()} style={{ maxWidth: "560px" }}>
        <div className="modal-header">
          <h3 style={{ margin: 0 }}>Conversion #{detail.id}</h3>
          <button className="modal-close" onClick={onClose}>×</button>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.4rem 1rem", fontSize: "0.82rem", marginBottom: "1rem" }}>
          {rows.map(([label, val]) => (
            <div key={label}>
              <span style={{ color: "var(--text-muted)" }}>{label}: </span>
              <span style={{ wordBreak: "break-all" }}>{val}</span>
            </div>
          ))}
        </div>

        {detail.capi_error && (
          <div style={{ background: "rgba(239,68,68,0.1)", border: "1px solid rgba(239,68,68,0.3)", borderRadius: "6px", padding: "0.6rem 0.75rem", fontSize: "0.8rem", color: "var(--danger)", marginBottom: "1rem" }}>
            {detail.capi_error}
          </div>
        )}

        {detail.capi_response && (
          <div style={{ background: "rgba(34,197,94,0.08)", border: "1px solid rgba(34,197,94,0.25)", borderRadius: "6px", padding: "0.6rem 0.75rem", fontSize: "0.75rem", fontFamily: "monospace", marginBottom: "1rem" }}>
            {JSON.stringify(detail.capi_response, null, 2)}
          </div>
        )}

        {(detail.capi_status === "failed" || detail.capi_status === "skipped") && (
          <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
            <button className="btn btn-primary" onClick={handleRetry} disabled={retrying}>
              {retrying ? "Retrying…" : "Retry CAPI Send"}
            </button>
            {retryMsg && (
              <span style={{ fontSize: "0.82rem", color: retryMsg.startsWith("Sent") ? "var(--success)" : "var(--danger)" }}>
                {retryMsg}
              </span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

const PAGE_SIZE = 25;

export default function ConversionsPage({ selectedAccount }: { selectedAccount: AdAccount | null }) {
  const [conversions, setConversions] = useState<Conversion[]>([]);
  const [stats, setStats] = useState<ConversionStats | null>(null);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [filterStatus, setFilterStatus] = useState("");
  const [filterSource, setFilterSource] = useState("");
  const [viewingId, setViewingId] = useState<number | null>(null);
  const [retryingId, setRetryingId] = useState<number | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const data = await getConversions({
        limit: PAGE_SIZE,
        offset,
        status: filterStatus || undefined,
        source: filterSource || undefined,
      });
      setConversions(data.conversions);
      setStats(data.stats);
      setTotal(data.total);
      setError("");
    } catch (e: any) {
      setError(e.message);
    }
  }, [offset, filterStatus, filterSource]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => { setOffset(0); }, [filterStatus, filterSource, selectedAccount]);

  const handleRetry = async (id: number) => {
    setRetryingId(id);
    try {
      await retryConversion(id);
      load();
    } catch (e: any) {
      alert(`Retry failed: ${e.message}`);
    } finally {
      setRetryingId(null);
    }
  };

  const totalPages = Math.ceil(total / PAGE_SIZE);
  const page = Math.floor(offset / PAGE_SIZE) + 1;

  const pixelNote = selectedAccount
    ? `Pixel for ${selectedAccount.account_name} (${selectedAccount.account_id})`
    : "Pixel: 1084399047241993 · default account";

  return (
    <main className="dashboard">
      {/* Stats row */}
      {stats && (
        <div style={{ display: "flex", gap: "0.75rem", flexWrap: "wrap", marginBottom: "0.25rem" }}>
          <StatCard label="Total Events"    value={String(stats.total)} />
          <StatCard label="Sent"            value={String(stats.total_sent)}   sub={`${stats.capi_success_rate.toFixed(1)}% success`} />
          <StatCard label="Failed"          value={String(stats.total_failed)} />
          <StatCard label="Match Rate"      value={`${stats.match_rate.toFixed(1)}%`} />
          <StatCard label="Revenue Tracked" value={fmtMoney(stats.total_revenue_tracked)} />
          <StatCard label="fbclid Rate"     value={`${stats.fbclid_rate.toFixed(1)}%`} sub="attribution" />
        </div>
      )}

      <div style={{ fontSize: "0.75rem", color: "var(--text-muted)", marginBottom: "0.5rem" }}>
        {pixelNote}
        {selectedAccount && (
          <span style={{ marginLeft: "0.5rem", color: "var(--warning)", fontSize: "0.72rem" }}>
            · CAPI events go to the configured pixel regardless of account selection
          </span>
        )}
      </div>

      {/* Conversion list */}
      <div className="card">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem", flexWrap: "wrap", gap: "0.5rem" }}>
          <h2 style={{ margin: 0 }}>CAPI Events</h2>
          <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
            <select
              className="form-select"
              style={{ width: "auto", fontSize: "0.8rem", padding: "0.3rem 0.6rem" }}
              value={filterStatus}
              onChange={(e) => setFilterStatus(e.target.value)}
            >
              <option value="">All statuses</option>
              <option value="sent">Sent</option>
              <option value="failed">Failed</option>
              <option value="skipped">Skipped</option>
              <option value="pending">Pending</option>
            </select>
            <select
              className="form-select"
              style={{ width: "auto", fontSize: "0.8rem", padding: "0.3rem 0.6rem" }}
              value={filterSource}
              onChange={(e) => setFilterSource(e.target.value)}
            >
              <option value="">All sources</option>
              <option value="webhook">Webhook</option>
              <option value="backfill">Backfill</option>
            </select>
            <span style={{ fontSize: "0.8rem", color: "var(--text-muted)" }}>{total} events</span>
          </div>
        </div>

        {error && <div className="error-banner" style={{ marginBottom: "1rem" }}>{error}</div>}

        {conversions.length === 0 ? (
          <p style={{ color: "var(--text-muted)" }}>No conversions found.</p>
        ) : (
          <>
            <div style={{ overflowX: "auto" }}>
              <table>
                <thead>
                  <tr>
                    <th>Date</th>
                    <th>Customer</th>
                    <th>Amount</th>
                    <th>GHL Match</th>
                    <th>fbclid</th>
                    <th>Status</th>
                    <th>Source</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {conversions.map((c) => (
                    <tr key={c.id} className={c.capi_status === "failed" ? "row-failed" : ""}>
                      <td style={{ fontSize: "0.78rem", color: "var(--text-muted)", whiteSpace: "nowrap" }}>
                        {fmtDate(c.created_at)}
                      </td>
                      <td>
                        <div style={{ fontWeight: 500 }}>{c.stripe_name || c.ghl_name || "—"}</div>
                        <div style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}>{c.stripe_email || "—"}</div>
                      </td>
                      <td style={{ fontWeight: 600, whiteSpace: "nowrap" }}>
                        {fmtMoney(c.amount, c.currency)}
                      </td>
                      <td>
                        <div style={{ fontSize: "0.8rem" }}>{c.match_method.replace(/_/g, " ")}</div>
                        {c.ghl_name && (
                          <div style={{ fontSize: "0.72rem", color: "var(--text-muted)" }}>{c.ghl_name}</div>
                        )}
                      </td>
                      <td style={{ textAlign: "center" }}>
                        {c.has_fbclid
                          ? <span style={{ color: "var(--success)", fontWeight: 700 }}>✓</span>
                          : <span style={{ color: "var(--text-muted)" }}>—</span>}
                      </td>
                      <td><StatusBadge status={c.capi_status} /></td>
                      <td>
                        <span style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}>{c.source}</span>
                      </td>
                      <td>
                        <div style={{ display: "flex", gap: "0.35rem" }}>
                          <button className="btn btn-sm" onClick={() => setViewingId(c.id)}>
                            View
                          </button>
                          {(c.capi_status === "failed" || c.capi_status === "skipped") && (
                            <button
                              className="btn btn-sm"
                              style={{ color: "var(--primary)", borderColor: "var(--primary)" }}
                              onClick={() => handleRetry(c.id)}
                              disabled={retryingId === c.id}
                            >
                              {retryingId === c.id ? "…" : "Retry"}
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {totalPages > 1 && (
              <div className="pagination">
                <button className="btn" disabled={page <= 1} onClick={() => setOffset(offset - PAGE_SIZE)}>← Prev</button>
                <span>Page {page} of {totalPages}</span>
                <button className="btn" disabled={page >= totalPages} onClick={() => setOffset(offset + PAGE_SIZE)}>Next →</button>
              </div>
            )}
          </>
        )}
      </div>

      {viewingId !== null && (
        <ConversionDetailModal id={viewingId} onClose={() => { setViewingId(null); load(); }} />
      )}
    </main>
  );
}
