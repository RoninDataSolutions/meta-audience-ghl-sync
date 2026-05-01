import { useState, useEffect } from "react";
import type { AdAccount } from "../types";
import {
  getAccounts,
  createAccount,
  updateAccount,
  deactivateAccount,
  testAccountToken,
} from "../api";

const CRON_PRESETS = [
  { label: "Manual only", value: "" },
  { label: "Daily at 6 AM", value: "0 6 * * *" },
  { label: "Weekly Monday 6 AM", value: "0 6 * * 1" },
  { label: "Weekly Friday 6 AM", value: "0 6 * * 5" },
];

function cronLabel(cron: string | null): string {
  if (!cron) return "Manual only";
  const preset = CRON_PRESETS.find((p) => p.value === cron);
  return preset ? preset.label : cron;
}

interface FormState {
  account_id: string;
  account_name: string;
  meta_access_token: string;
  notification_email: string;
  audit_cron: string;
  custom_cron: boolean;
}

const emptyForm = (): FormState => ({
  account_id: "",
  account_name: "",
  meta_access_token: "",
  notification_email: "",
  audit_cron: "",
  custom_cron: false,
});

export default function AccountsPage() {
  const [accounts, setAccounts] = useState<AdAccount[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [editId, setEditId] = useState<number | null>(null);
  const [form, setForm] = useState<FormState>(emptyForm());
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [testResults, setTestResults] = useState<Record<number, string>>({});

  const load = async () => {
    try {
      const data = await getAccounts();
      setAccounts(data.accounts);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const openAdd = () => {
    setEditId(null);
    setForm(emptyForm());
    setError("");
    setShowForm(true);
  };

  const openEdit = (a: AdAccount) => {
    setEditId(a.id);
    setForm({
      account_id: a.account_id,
      account_name: a.account_name,
      meta_access_token: "",
      notification_email: a.notification_email || "",
      audit_cron: a.audit_cron || "",
      custom_cron: !CRON_PRESETS.some((p) => p.value === (a.audit_cron || "")),
    });
    setError("");
    setShowForm(true);
  };

  const handleSave = async () => {
    setError("");
    setSaving(true);
    try {
      const cronValue = form.audit_cron || undefined;
      if (editId !== null) {
        await updateAccount(editId, {
          account_name: form.account_name,
          meta_access_token: form.meta_access_token || undefined,
          notification_email: form.notification_email || undefined,
          audit_cron: cronValue,
        });
      } else {
        await createAccount({
          account_id: form.account_id,
          account_name: form.account_name,
          meta_access_token: form.meta_access_token || undefined,
          notification_email: form.notification_email || undefined,
          audit_cron: cronValue,
        });
      }
      setShowForm(false);
      load();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  };

  const handleDeactivate = async (id: number) => {
    if (!confirm("Deactivate this account?")) return;
    try {
      await deactivateAccount(id);
      load();
    } catch (e: any) {
      alert(e.message);
    }
  };

  const handleTest = async (id: number) => {
    setTestResults((r) => ({ ...r, [id]: "testing…" }));
    try {
      const result = await testAccountToken(id);
      setTestResults((r) => ({
        ...r,
        [id]: result.status === "ok"
          ? `✓ ${result.account_name}`
          : `✗ ${result.detail}`,
      }));
    } catch (e: any) {
      setTestResults((r) => ({ ...r, [id]: `✗ ${e.message}` }));
    }
  };

  if (loading) return <div className="dashboard" style={{ padding: "2rem" }}>Loading accounts…</div>;

  return (
    <main className="dashboard">
      <div className="card">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem" }}>
          <h2 style={{ margin: 0 }}>Ad Accounts</h2>
          <button className="btn btn-primary" onClick={openAdd}>+ Add Account</button>
        </div>

        {accounts.length === 0 ? (
          <p style={{ color: "var(--text-muted)" }}>
            No accounts configured. Add one to enable multi-account audits.
            The default account from <code>.env</code> is always available for audits.
          </p>
        ) : (
          <table className="history-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Account ID</th>
                <th>Token</th>
                <th>Schedule</th>
                <th>Last Audit</th>
                <th>Status</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {accounts.map((a) => (
                <tr key={a.id}>
                  <td>{a.account_name}</td>
                  <td><code style={{ fontSize: "0.8rem" }}>{a.account_id}</code></td>
                  <td>
                    <span className={`badge ${a.has_custom_token ? "badge-success" : "badge-neutral"}`}>
                      {a.has_custom_token ? "Custom" : "Default"}
                    </span>
                  </td>
                  <td style={{ fontSize: "0.85rem" }}>{cronLabel(a.audit_cron)}</td>
                  <td style={{ fontSize: "0.85rem" }}>
                    {a.last_audit_at ? new Date(a.last_audit_at).toLocaleDateString() : "—"}
                  </td>
                  <td>
                    <span className={`badge ${a.is_active ? "badge-success" : "badge-neutral"}`}>
                      {a.is_active ? "Active" : "Inactive"}
                    </span>
                  </td>
                  <td>
                    <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
                      <button className="btn btn-sm" onClick={() => openEdit(a)}>Edit</button>
                      <button className="btn btn-sm" onClick={() => handleTest(a.id)}>Test</button>
                      {a.is_active && (
                        <button className="btn btn-sm btn-danger" onClick={() => handleDeactivate(a.id)}>
                          Deactivate
                        </button>
                      )}
                    </div>
                    {testResults[a.id] && (
                      <div style={{ fontSize: "0.75rem", marginTop: "0.25rem", color: testResults[a.id].startsWith("✓") ? "var(--success)" : "var(--danger)" }}>
                        {testResults[a.id]}
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {showForm && (
        <div className="modal-overlay" onClick={() => setShowForm(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()} style={{ maxWidth: "520px" }}>
            <div className="modal-header">
              <h3>{editId !== null ? "Edit Account" : "Add Account"}</h3>
              <button className="modal-close" onClick={() => setShowForm(false)}>×</button>
            </div>
            <div className="modal-body">
              {error && <div className="error-banner" style={{ marginBottom: "1rem" }}>{error}</div>}

              {editId === null && (
                <div className="form-group">
                  <label>Account ID *</label>
                  <input
                    type="text"
                    className="form-input"
                    placeholder="act_123456789"
                    value={form.account_id}
                    onChange={(e) => setForm({ ...form, account_id: e.target.value })}
                  />
                </div>
              )}

              <div className="form-group">
                <label>Display Name *</label>
                <input
                  type="text"
                  className="form-input"
                  placeholder="My Business"
                  value={form.account_name}
                  onChange={(e) => setForm({ ...form, account_name: e.target.value })}
                />
              </div>

              <div className="form-group">
                <label>Meta Access Token</label>
                <input
                  type="password"
                  className="form-input"
                  placeholder={editId !== null ? "Leave blank to keep existing token" : "Leave blank to use default from .env"}
                  value={form.meta_access_token}
                  onChange={(e) => setForm({ ...form, meta_access_token: e.target.value })}
                />
              </div>

              <div className="form-group">
                <label>Notification Email</label>
                <input
                  type="email"
                  className="form-input"
                  placeholder="Falls back to global notification email"
                  value={form.notification_email}
                  onChange={(e) => setForm({ ...form, notification_email: e.target.value })}
                />
              </div>

              <div className="form-group">
                <label>Audit Schedule</label>
                <select
                  className="form-select"
                  value={form.custom_cron ? "custom" : form.audit_cron}
                  onChange={(e) => {
                    if (e.target.value === "custom") {
                      setForm({ ...form, custom_cron: true, audit_cron: "" });
                    } else {
                      setForm({ ...form, custom_cron: false, audit_cron: e.target.value });
                    }
                  }}
                >
                  {CRON_PRESETS.map((p) => (
                    <option key={p.value} value={p.value}>{p.label}</option>
                  ))}
                  <option value="custom">Custom cron…</option>
                </select>
                {form.custom_cron && (
                  <input
                    type="text"
                    className="form-input"
                    style={{ marginTop: "0.5rem" }}
                    placeholder="0 6 * * 1"
                    value={form.audit_cron}
                    onChange={(e) => setForm({ ...form, audit_cron: e.target.value })}
                  />
                )}
              </div>

              <div style={{ display: "flex", gap: "0.75rem", justifyContent: "flex-end", marginTop: "1.5rem" }}>
                <button className="btn" onClick={() => setShowForm(false)}>Cancel</button>
                <button className="btn btn-primary" onClick={handleSave} disabled={saving}>
                  {saving ? "Saving…" : editId !== null ? "Save" : "Test & Save"}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </main>
  );
}
