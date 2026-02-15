import type { SyncStatus } from "../types";

interface Props {
  syncStatus: SyncStatus | null;
  onSyncNow: () => void;
  syncing: boolean;
  locationName: string;
}

export default function Header({ syncStatus, onSyncNow, syncing, locationName }: Props) {
  const lastRun = syncStatus?.last_run;
  const isRunning = syncStatus?.is_running || syncing;

  const statusBadge = () => {
    if (isRunning) return <span className="badge running">Running</span>;
    if (!lastRun) return <span className="badge neutral">No syncs yet</span>;
    if (lastRun.status === "success")
      return <span className="badge success">Success</span>;
    return <span className="badge failed">Failed</span>;
  };

  const lastSyncTime = () => {
    if (!lastRun?.completed_at) return null;
    const d = new Date(lastRun.completed_at);
    return d.toLocaleString();
  };

  return (
    <header className="app-header">
      <div className="header-left">
        <h1>GHL → Meta Audience Sync</h1>
        {locationName && (
          <span className="location-name">{locationName}</span>
        )}
        <div className="header-status">
          {statusBadge()}
          {lastSyncTime() && (
            <span className="last-sync">Last sync: {lastSyncTime()}</span>
          )}
        </div>
      </div>
      <button
        className="btn btn-primary"
        onClick={onSyncNow}
        disabled={isRunning}
      >
        {isRunning ? "Syncing..." : "Sync Now"}
      </button>
    </header>
  );
}
