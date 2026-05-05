import { useState, useEffect, useCallback } from "react";
import type { SyncConfig, SyncStatus, SyncRun, AdAccount } from "./types";
import { getConfig, getSyncStatus, getSyncHistory, triggerSync, getAccounts } from "./api";
import Header from "./components/Header";
import ConfigPanel from "./components/ConfigPanel";
import StatusPanel from "./components/StatusPanel";
import SyncHistory from "./components/SyncHistory";
import SyncDetailModal from "./components/SyncDetailModal";
import ValueChart from "./components/ValueChart";
import EmailSettings from "./components/EmailSettings";
import AuditPage from "./pages/AuditPage";
import AccountsPage from "./pages/AccountsPage";
import ConversionsPage from "./pages/ConversionsPage";

type Tab = "sync" | "audit" | "conversions" | "accounts";

export default function App() {
  const [activeTab, setActiveTab] = useState<Tab>("sync");
  const [config, setConfig] = useState<SyncConfig | null>(null);
  const [metaAdAccountId, setMetaAdAccountId] = useState("");
  const [locationName, setLocationName] = useState("");
  const [smtpFrom, setSmtpFrom] = useState("");
  const [smtpTo, setSmtpTo] = useState("");
  const [syncStatus, setSyncStatus] = useState<SyncStatus | null>(null);
  const [history, setHistory] = useState<SyncRun[]>([]);
  const [historyPage, setHistoryPage] = useState(1);
  const [totalPages, setTotalPages] = useState(0);
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [accounts, setAccounts] = useState<AdAccount[]>([]);
  const [selectedAccount, setSelectedAccount] = useState<AdAccount | null>(null);

  const accountId = selectedAccount?.account_id;

  const loadConfig = useCallback(async () => {
    try {
      const data = await getConfig(accountId);
      setConfig(data.config);
      setMetaAdAccountId(data.meta_ad_account_id);
      setLocationName(data.ghl_location_name || "");
      setSmtpFrom(data.smtp_from || "");
      setSmtpTo(data.smtp_to || "");
    } catch (e) {
      console.error("Failed to load config:", e);
    }
  }, [accountId]);

  const loadStatus = useCallback(async () => {
    try {
      const status = await getSyncStatus(accountId);
      setSyncStatus(status);
      if (!status.is_running && syncing) {
        setSyncing(false);
        loadHistory();
      }
    } catch (e) {
      console.error("Failed to load status:", e);
    }
  }, [accountId, syncing]);

  const loadHistory = useCallback(async () => {
    try {
      const data = await getSyncHistory(historyPage, accountId);
      setHistory(data.runs);
      setTotalPages(data.total_pages);
    } catch (e) {
      console.error("Failed to load history:", e);
    }
  }, [historyPage, accountId]);

  useEffect(() => {
    loadConfig();
    loadStatus();
    loadHistory();
    getAccounts().then((d) => setAccounts(d.accounts)).catch(() => {});
  }, []);

  // Reload all sync data when account changes
  useEffect(() => {
    setHistoryPage(1);
    loadConfig();
    loadStatus();
    loadHistory();
  }, [accountId]);

  useEffect(() => {
    loadHistory();
  }, [historyPage]);

  // Polling: 5s when syncing, 30s otherwise
  useEffect(() => {
    const interval = setInterval(
      () => {
        loadStatus();
        if (syncing) loadHistory();
      },
      syncing ? 5000 : 30000
    );
    return () => clearInterval(interval);
  }, [syncing, loadStatus]);

  const handleSyncNow = async () => {
    try {
      setSyncing(true);
      await triggerSync(accountId);
      loadStatus();
    } catch (e: any) {
      alert(e.message);
      setSyncing(false);
    }
  };

  // Get distribution from last successful run
  const lastSuccessRun = history.find((r) => r.status === "success");
  const distribution =
    lastSuccessRun?.normalization_stats?.distribution || null;

  return (
    <div className="app">
      <Header
        syncStatus={syncStatus}
        onSyncNow={handleSyncNow}
        syncing={syncing}
        locationName={locationName}
        accounts={accounts}
        selectedAccount={selectedAccount}
        onSelectAccount={setSelectedAccount}
      />

      <nav className="tab-nav">
        {(["sync", "audit", "conversions", "accounts"] as Tab[]).map((tab) => (
          <button
            key={tab}
            className={`tab-btn${activeTab === tab ? " tab-btn--active" : ""}`}
            onClick={() => setActiveTab(tab)}
          >
            {tab === "sync" ? "Sync" : tab === "audit" ? "Audit" : tab === "conversions" ? "Conversions" : "Accounts"}
          </button>
        ))}
      </nav>

      {activeTab === "sync" && (
        <main className="dashboard">
          <div className="dashboard-grid">
            <ConfigPanel config={config} onSaved={loadConfig} accountId={accountId} />
            <StatusPanel
              lastRun={syncStatus?.last_run || null}
              metaAdAccountId={metaAdAccountId}
            />
          </div>

          <SyncHistory
            runs={history}
            page={historyPage}
            totalPages={totalPages}
            onPageChange={setHistoryPage}
            onViewDetail={setSelectedRunId}
          />

          <div className="dashboard-grid">
            <ValueChart distribution={distribution} />
            <EmailSettings smtpFrom={smtpFrom} smtpTo={smtpTo} />
          </div>
        </main>
      )}

      {activeTab === "audit" && <AuditPage selectedAccount={selectedAccount} />}
      {activeTab === "conversions" && <ConversionsPage selectedAccount={selectedAccount} />}
      {activeTab === "accounts" && <AccountsPage onAccountsChanged={() => getAccounts().then((d) => setAccounts(d.accounts))} />}

      {selectedRunId !== null && (
        <SyncDetailModal
          runId={selectedRunId}
          onClose={() => setSelectedRunId(null)}
        />
      )}
    </div>
  );
}
