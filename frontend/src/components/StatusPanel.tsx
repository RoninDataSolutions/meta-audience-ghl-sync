import type { SyncRun } from "../types";

interface Props {
  lastRun: SyncRun | null;
  metaAdAccountId: string;
}

export default function StatusPanel({ lastRun, metaAdAccountId }: Props) {
  const matchRate =
    lastRun && lastRun.contacts_processed > 0
      ? ((lastRun.contacts_matched / lastRun.contacts_processed) * 100).toFixed(
          1
        )
      : "—";

  return (
    <div className="card">
      <h2>Current Status</h2>
      <div className="status-grid">
        <div className="status-item">
          <span className="status-label">Meta Ad Account</span>
          <span className="status-value">{metaAdAccountId || "—"}</span>
        </div>
        <div className="status-item">
          <span className="status-label">Custom Audience</span>
          <span className="status-value">
            {lastRun?.meta_audience_name || "—"}
          </span>
        </div>
        <div className="status-item">
          <span className="status-label">Lookalike Audience</span>
          <span className="status-value">
            {lastRun?.meta_lookalike_name || "—"}
          </span>
        </div>
        <div className="status-item">
          <span className="status-label">Contacts Synced</span>
          <span className="status-value">
            {lastRun?.contacts_processed ?? "—"}
          </span>
        </div>
        <div className="status-item">
          <span className="status-label">Match Rate</span>
          <span className="status-value">{matchRate}%</span>
        </div>
      </div>
    </div>
  );
}
