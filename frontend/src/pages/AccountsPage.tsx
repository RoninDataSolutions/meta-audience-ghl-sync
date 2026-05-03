import { useState, useEffect } from "react";
import type { AdAccount } from "../types";
import {
  getAccounts,
  createAccount,
  updateAccount,
  deactivateAccount,
  testAccountToken,
} from "../api";
import BusinessProfileWizard from "../components/BusinessProfileWizard";

function isProfileComplete(account: AdAccount): boolean {
  const bp = account.business_profile;
  return !!(bp?.industry && bp?.primary_goal);
}

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

const PRIMARY_GOALS = [
  { label: "Select goal…", value: "" },
  { label: "Lead Generation", value: "leads" },
  { label: "Direct Purchase — Package / Class / Product", value: "purchases" },
  { label: "Bookings / Appointments", value: "bookings" },
  { label: "Recurring Membership / Subscription", value: "membership" },
  { label: "App Installs", value: "app_installs" },
  { label: "Brand Awareness", value: "awareness" },
  { label: "Course / Digital Product Sales", value: "course_sales" },
  { label: "Consulting / High-Ticket Services", value: "consulting" },
];

interface FormState {
  account_id: string;
  account_name: string;
  meta_access_token: string;
  notification_email: string;
  audit_cron: string;
  custom_cron: boolean;
  // Business profile
  website_url: string;
  industry: string;
  description: string;
  target_customer: string;
  avg_order_value: string;
  primary_goal: string;
  facebook_page_id: string;
  competitor_page_ids: string;
}

const emptyForm = (): FormState => ({
  account_id: "",
  account_name: "",
  meta_access_token: "",
  notification_email: "",
  audit_cron: "",
  custom_cron: false,
  website_url: "",
  industry: "",
  description: "",
  target_customer: "",
  avg_order_value: "",
  primary_goal: "",
  facebook_page_id: "",
  competitor_page_ids: "",
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
  const [wizardAccount, setWizardAccount] = useState<AdAccount | null>(null);

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
    const bp = a.business_profile || {};
    setForm({
      account_id: a.account_id,
      account_name: a.account_name,
      meta_access_token: "",
      notification_email: a.notification_email || "",
      audit_cron: a.audit_cron || "",
      custom_cron: !CRON_PRESETS.some((p) => p.value === (a.audit_cron || "")),
      website_url: a.website_url || "",
      industry: bp.industry || "",
      description: bp.description || "",
      target_customer: bp.target_customer || "",
      avg_order_value: bp.avg_order_value != null ? String(bp.avg_order_value) : "",
      primary_goal: bp.primary_goal || "",
      facebook_page_id: bp.facebook_page_id || "",
      competitor_page_ids: bp.competitor_page_ids || "",
    });
    setError("");
    setShowForm(true);
  };

  const handleSave = async () => {
    setError("");
    setSaving(true);
    try {
      const cronValue = form.audit_cron || undefined;
      const bp = {
        ...(form.industry && { industry: form.industry }),
        ...(form.description && { description: form.description }),
        ...(form.target_customer && { target_customer: form.target_customer }),
        ...(form.avg_order_value && { avg_order_value: parseFloat(form.avg_order_value) }),
        ...(form.primary_goal && { primary_goal: form.primary_goal }),
        ...(form.facebook_page_id && { facebook_page_id: form.facebook_page_id }),
        ...(form.competitor_page_ids && { competitor_page_ids: form.competitor_page_ids }),
      };
      if (editId !== null) {
        await updateAccount(editId, {
          account_name: form.account_name,
          meta_access_token: form.meta_access_token || undefined,
          notification_email: form.notification_email || undefined,
          audit_cron: cronValue,
          website_url: form.website_url || undefined,
          business_profile: Object.keys(bp).length ? bp : undefined,
        });
        setShowForm(false);
        load();
      } else {
        await createAccount({
          account_id: form.account_id,
          account_name: form.account_name,
          meta_access_token: form.meta_access_token || undefined,
          notification_email: form.notification_email || undefined,
          audit_cron: cronValue,
          website_url: form.website_url || undefined,
          business_profile: Object.keys(bp).length ? bp : undefined,
        });
        setShowForm(false);
        const refreshed = await getAccounts();
        const newAccount = refreshed.accounts.find((a) => a.account_id === form.account_id);
        // Auto-open wizard if no business profile was filled in
        if (newAccount && !isProfileComplete(newAccount)) {
          setWizardAccount(newAccount);
        }
        setAccounts(refreshed.accounts);
        return;
      }
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

  const incompleteCount = accounts.filter((a) => a.is_active && !isProfileComplete(a)).length;

  return (
    <main className="dashboard">
      {incompleteCount > 0 && (
        <div style={{
          background: "rgba(217,119,6,0.12)",
          border: "1px solid rgba(217,119,6,0.35)",
          borderRadius: "8px",
          padding: "0.75rem 1rem",
          marginBottom: "1rem",
          fontSize: "0.85rem",
          display: "flex",
          alignItems: "center",
          gap: "0.5rem",
        }}>
          <span style={{ color: "#d97706", fontWeight: 700 }}>⚠</span>
          <span>
            <strong>{incompleteCount} account{incompleteCount !== 1 ? "s" : ""}</strong> {incompleteCount !== 1 ? "are" : "is"} missing a business profile.
            AI audit analysis and campaign recommendations improve significantly with profile data.
          </span>
        </div>
      )}

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
                <th>Profile</th>
                <th>Schedule</th>
                <th>Last Audit</th>
                <th>Status</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {accounts.map((a) => {
                const complete = isProfileComplete(a);
                return (
                  <tr key={a.id}>
                    <td>{a.account_name}</td>
                    <td><code style={{ fontSize: "0.8rem" }}>{a.account_id}</code></td>
                    <td>
                      <span className={`badge ${a.has_custom_token ? "badge-success" : "badge-neutral"}`}>
                        {a.has_custom_token ? "Custom" : "Default"}
                      </span>
                    </td>
                    <td>
                      <span className={`badge ${complete ? "badge-success" : "badge-neutral"}`} title={complete ? `${a.business_profile?.industry} · ${a.business_profile?.primary_goal}` : "No business profile"}>
                        {complete ? "Complete" : "Incomplete"}
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
                      <div style={{ display: "flex", gap: "0.4rem", flexWrap: "wrap" }}>
                        <button className="btn btn-sm" onClick={() => openEdit(a)}>Edit</button>
                        <button
                          className="btn btn-sm"
                          style={!complete ? { background: "rgba(217,119,6,0.15)", borderColor: "#d97706", color: "#d97706" } : {}}
                          onClick={() => setWizardAccount(a)}
                        >
                          {complete ? "Profile" : "Setup Profile"}
                        </button>
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
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      {wizardAccount && (
        <BusinessProfileWizard
          account={wizardAccount}
          onSave={async () => {
            setWizardAccount(null);
            load();
          }}
          onClose={() => setWizardAccount(null)}
        />
      )}

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

              {/* Business Profile */}
              <div style={{ borderTop: "1px solid var(--border)", paddingTop: "1rem", marginTop: "0.5rem" }}>
                <p style={{ fontSize: "0.75rem", color: "var(--text-muted)", marginBottom: "0.75rem", fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.05em" }}>
                  Business Profile — used by AI to make grounded assessments
                </p>

                <div className="form-group">
                  <label>Website URL</label>
                  <input
                    type="url"
                    className="form-input"
                    placeholder="https://yourbusiness.com"
                    value={form.website_url}
                    onChange={(e) => setForm({ ...form, website_url: e.target.value })}
                  />
                </div>

                <div className="form-group">
                  <label>Industry</label>
                  <input
                    type="text"
                    className="form-input"
                    placeholder="e.g. Fitness & Wellness, E-commerce, Real Estate"
                    value={form.industry}
                    onChange={(e) => setForm({ ...form, industry: e.target.value })}
                  />
                </div>

                <div className="form-group">
                  <label>Business Description</label>
                  <input
                    type="text"
                    className="form-input"
                    placeholder="e.g. Online prenatal yoga courses for expecting mothers"
                    value={form.description}
                    onChange={(e) => setForm({ ...form, description: e.target.value })}
                  />
                </div>

                <div className="form-group">
                  <label>Target Customer</label>
                  <input
                    type="text"
                    className="form-input"
                    placeholder="e.g. Women 25–40, pregnant or postpartum"
                    value={form.target_customer}
                    onChange={(e) => setForm({ ...form, target_customer: e.target.value })}
                  />
                </div>

                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.75rem" }}>
                  <div className="form-group">
                    <label>Avg Order Value ($)</label>
                    <input
                      type="number"
                      className="form-input"
                      placeholder="97"
                      value={form.avg_order_value}
                      onChange={(e) => setForm({ ...form, avg_order_value: e.target.value })}
                    />
                  </div>
                  <div className="form-group">
                    <label>Primary Goal</label>
                    <select
                      className="form-select"
                      value={form.primary_goal}
                      onChange={(e) => setForm({ ...form, primary_goal: e.target.value })}
                    >
                      {PRIMARY_GOALS.map((g) => (
                        <option key={g.value} value={g.value}>{g.label}</option>
                      ))}
                    </select>
                  </div>
                </div>

                <div className="form-group">
                  <label>Facebook Page ID</label>
                  <input
                    type="text"
                    className="form-input"
                    placeholder="e.g. 123456789 — enables page stats + ad library fetch"
                    value={form.facebook_page_id}
                    onChange={(e) => setForm({ ...form, facebook_page_id: e.target.value })}
                  />
                </div>

                <div className="form-group">
                  <label>Competitor Page IDs</label>
                  <input
                    type="text"
                    className="form-input"
                    placeholder="Comma-separated Facebook page IDs of competitors"
                    value={form.competitor_page_ids}
                    onChange={(e) => setForm({ ...form, competitor_page_ids: e.target.value })}
                  />
                </div>
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
