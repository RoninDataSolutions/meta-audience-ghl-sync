import type { SyncStatus, AdAccount } from "../types";

interface Props {
  syncStatus: SyncStatus | null;
  onSyncNow: () => void;
  syncing: boolean;
  locationName: string;
  accounts: AdAccount[];
  selectedAccount: AdAccount | null;
  onSelectAccount: (account: AdAccount | null) => void;
}

export default function Header({
  syncStatus,
  onSyncNow,
  syncing,
  locationName,
  accounts,
  selectedAccount,
  onSelectAccount,
}: Props) {
  const lastRun = syncStatus?.last_run;
  const isRunning = syncStatus?.is_running || syncing;

  const statusBadge = () => {
    if (isRunning) return <span className="badge running">Running</span>;
    if (!lastRun) return <span className="badge neutral">No syncs yet</span>;
    if (lastRun.status === "success") return <span className="badge success">Success</span>;
    return <span className="badge failed">Failed</span>;
  };

  const lastSyncTime = () => {
    if (!lastRun?.completed_at) return null;
    return new Date(lastRun.completed_at).toLocaleString();
  };

  return (
    <header className="app-header">
      <div className="header-left">
        <h1>GHL → Meta Sync</h1>
        {locationName && <span className="location-name">{locationName}</span>}
        <div className="header-status">
          {statusBadge()}
          {lastSyncTime() && <span className="last-sync">Last sync: {lastSyncTime()}</span>}
        </div>
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
        <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: "0.2rem" }}>
          <span style={{ fontSize: "0.7rem", color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.05em" }}>
            Account
          </span>
          <select
            style={{
              background: "var(--bg)",
              border: "1px solid var(--border)",
              borderRadius: "0.375rem",
              color: "var(--text)",
              fontSize: "0.85rem",
              padding: "0.3rem 0.6rem",
              cursor: "pointer",
              minWidth: "160px",
            }}
            value={selectedAccount?.account_id ?? ""}
            onChange={(e) => {
              const found = accounts.find((a) => a.account_id === e.target.value) ?? null;
              onSelectAccount(found);
            }}
          >
            <option value="">Default (.env)</option>
            {accounts.filter((a) => a.is_active).map((a) => (
              <option key={a.id} value={a.account_id}>{a.account_name}</option>
            ))}
          </select>
        </div>

        <button
          className="btn btn-primary"
          onClick={onSyncNow}
          disabled={isRunning}
        >
          {isRunning ? "Syncing..." : "Sync Now"}
        </button>
      </div>
    </header>
  );
}
