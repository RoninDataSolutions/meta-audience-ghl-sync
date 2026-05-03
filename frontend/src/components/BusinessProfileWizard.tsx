import { useState } from "react";
import type { AdAccount } from "../types";
import { updateAccount } from "../api";

// ── Types ────────────────────────────────────────────────────────────────────

interface WizardState {
  industry: string;
  description: string;
  business_notes: string;
  target_customer: string;
  avg_order_value: string;
  primary_goal: string;
  facebook_page_id: string;
  competitor_page_ids: string;
  website_url: string;
}

interface Props {
  account: AdAccount;
  onSave: () => void;
  onClose: () => void;
}

// ── Constants ────────────────────────────────────────────────────────────────

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

const STEPS = [
  { id: "overview",  title: "Business Overview",  subtitle: "What does your business do?" },
  { id: "model",     title: "Business Model",      subtitle: "How it actually works — pricing, repurchase, nuances" },
  { id: "customer",  title: "Your Customer",       subtitle: "Who are you selling to?" },
  { id: "goals",     title: "Ad Goals",            subtitle: "What do you want Meta ads to achieve?" },
  { id: "presence",  title: "Meta Presence",       subtitle: "Your Facebook page and competitors" },
  { id: "website",   title: "Website",             subtitle: "Your business website" },
  { id: "review",    title: "Review & Save",       subtitle: "Confirm your profile before saving" },
];

// ── Component ────────────────────────────────────────────────────────────────

export default function BusinessProfileWizard({ account, onSave, onClose }: Props) {
  const bp = account.business_profile || {};

  const [step, setStep] = useState(0);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const [form, setForm] = useState<WizardState>({
    industry:            bp.industry            || "",
    description:         bp.description         || "",
    business_notes:      account.business_notes || "",
    target_customer:     bp.target_customer     || "",
    avg_order_value:     bp.avg_order_value != null ? String(bp.avg_order_value) : "",
    primary_goal:        bp.primary_goal        || "",
    facebook_page_id:    bp.facebook_page_id    || "",
    competitor_page_ids: bp.competitor_page_ids || "",
    website_url:         account.website_url    || "",
  });

  const set = (key: keyof WizardState, value: string) =>
    setForm((f) => ({ ...f, [key]: value }));

  const canProceed = (): boolean => {
    switch (step) {
      case 0: return form.industry.trim().length > 0;
      case 2: return form.target_customer.trim().length > 0;
      case 3: return form.primary_goal.length > 0;
      default: return true;
    }
  };

  const handleSave = async () => {
    setError("");
    setSaving(true);
    try {
      const bp_payload: Record<string, unknown> = {
        ...(form.industry            && { industry: form.industry }),
        ...(form.description         && { description: form.description }),
        ...(form.target_customer     && { target_customer: form.target_customer }),
        ...(form.avg_order_value     && { avg_order_value: parseFloat(form.avg_order_value) }),
        ...(form.primary_goal        && { primary_goal: form.primary_goal }),
        ...(form.facebook_page_id    && { facebook_page_id: form.facebook_page_id }),
        ...(form.competitor_page_ids && { competitor_page_ids: form.competitor_page_ids }),
      };
      await updateAccount(account.id, {
        business_profile: bp_payload,
        website_url:      form.website_url    || undefined,
        business_notes:   form.business_notes || undefined,
      });
      onSave();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  };

  const goalLabel = PRIMARY_GOALS.find((g) => g.value === form.primary_goal)?.label || "—";

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal"
        onClick={(e) => e.stopPropagation()}
        style={{ maxWidth: "560px", width: "100%" }}
      >
        {/* Header */}
        <div className="modal-header">
          <div>
            <h3 style={{ margin: 0 }}>Business Profile Setup</h3>
            <div style={{ fontSize: "0.8rem", color: "var(--text-muted)", marginTop: "0.2rem" }}>
              {account.account_name} · {account.account_id}
            </div>
          </div>
          <button className="modal-close" onClick={onClose}>×</button>
        </div>

        {/* Step progress bar */}
        <div style={{ padding: "0.75rem 1.5rem 0", borderBottom: "1px solid var(--border, rgba(255,255,255,0.1))" }}>
          <div style={{ display: "flex", gap: "4px", marginBottom: "0.6rem" }}>
            {STEPS.map((s, i) => (
              <div
                key={s.id}
                style={{
                  flex: 1,
                  height: "3px",
                  borderRadius: "2px",
                  background: i <= step
                    ? "var(--primary, #3b82f6)"
                    : "var(--border, rgba(255,255,255,0.15))",
                  transition: "background 0.2s",
                }}
              />
            ))}
          </div>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", paddingBottom: "0.6rem" }}>
            <span style={{ fontSize: "0.75rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--primary, #3b82f6)" }}>
              Step {step + 1} of {STEPS.length} — {STEPS[step].title}
            </span>
            <span style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}>{STEPS[step].subtitle}</span>
          </div>
        </div>

        {/* Body */}
        <div className="modal-body">
          {error && <div className="error-banner" style={{ marginBottom: "1rem" }}>{error}</div>}

          {/* Step 0 — Business Overview */}
          {step === 0 && (
            <div>
              <div className="form-group">
                <label>Industry *</label>
                <input
                  type="text"
                  className="form-input"
                  placeholder="e.g. Fitness & Wellness, E-commerce, Real Estate, SaaS"
                  value={form.industry}
                  autoFocus
                  onChange={(e) => set("industry", e.target.value)}
                />
                <div style={{ fontSize: "0.75rem", color: "var(--text-muted)", marginTop: "0.3rem" }}>
                  Used to benchmark CPA/ROAS against realistic expectations for your industry.
                </div>
              </div>
              <div className="form-group">
                <label>Business Description</label>
                <textarea
                  className="form-input"
                  style={{ minHeight: "80px", resize: "vertical" }}
                  placeholder="e.g. Group prenatal and postnatal yoga classes in Austin, TX"
                  value={form.description}
                  onChange={(e) => set("description", e.target.value)}
                />
                <div style={{ fontSize: "0.75rem", color: "var(--text-muted)", marginTop: "0.3rem" }}>
                  Helps the AI understand your offer and assess message-to-market fit.
                </div>
              </div>
            </div>
          )}

          {/* Step 1 — Business Model & Notes */}
          {step === 1 && (
            <div>
              <div style={{
                background: "rgba(59,130,246,0.08)",
                border: "1px solid rgba(59,130,246,0.2)",
                borderRadius: "6px",
                padding: "0.75rem 1rem",
                marginBottom: "1rem",
                fontSize: "0.82rem",
                lineHeight: 1.5,
              }}>
                <strong>This is the most important field.</strong> The AI uses this to move from generic
                paid-media advice to recommendations that fit how your business actually works. Be specific —
                pricing model, how customers buy, what triggers a repurchase, what your upsells are,
                seasonal patterns, anything that isn't obvious from your industry/description.
              </div>
              <div className="form-group">
                <label>Business Model & Operating Notes</label>
                <textarea
                  className="form-input"
                  style={{ minHeight: "160px", resize: "vertical" }}
                  autoFocus
                  placeholder={
                    "Examples:\n\n" +
                    "• Customers buy group class packages (5-class or 10-class). When the package expires they must repurchase. No subscription. High-LTV customers buy 4–6 packages per year.\n\n" +
                    "• Main upsell: upgrade from 5-class ($89) to 10-class ($159) at checkout.\n\n" +
                    "• Lapsed customers (no purchase in 60+ days) are the highest-converting retargeting audience.\n\n" +
                    "• Demand peaks Jan–Mar (resolutions) and Sep–Oct (back-to-routine). Summer is slow.\n\n" +
                    "• Prenatal customers typically start 2nd trimester, postnatal return around 6 weeks postpartum."
                  }
                  value={form.business_notes}
                  onChange={(e) => set("business_notes", e.target.value)}
                />
                <div style={{ fontSize: "0.75rem", color: "var(--text-muted)", marginTop: "0.3rem" }}>
                  Saved permanently on this account. Included in every future audit so projections and
                  campaign suggestions are grounded in your actual business model.
                </div>
              </div>
            </div>
          )}

          {/* Step 2 — Your Customer */}
          {step === 2 && (
            <div>
              <div className="form-group">
                <label>Target Customer *</label>
                <input
                  type="text"
                  className="form-input"
                  placeholder="e.g. Women 25–40, pregnant or postpartum, interested in wellness"
                  value={form.target_customer}
                  autoFocus
                  onChange={(e) => set("target_customer", e.target.value)}
                />
                <div style={{ fontSize: "0.75rem", color: "var(--text-muted)", marginTop: "0.3rem" }}>
                  Demographic targeting suggestions will be based on this.
                </div>
              </div>
              <div className="form-group">
                <label>Average Order / Transaction Value ($)</label>
                <input
                  type="number"
                  className="form-input"
                  placeholder="e.g. 120"
                  value={form.avg_order_value}
                  onChange={(e) => set("avg_order_value", e.target.value)}
                />
                <div style={{ fontSize: "0.75rem", color: "var(--text-muted)", marginTop: "0.3rem" }}>
                  If customers repurchase, use the first-purchase value here and describe the repurchase
                  model in Business Notes above — CPA targets will be calibrated accordingly.
                </div>
              </div>
            </div>
          )}

          {/* Step 3 — Ad Goals */}
          {step === 3 && (
            <div>
              <div className="form-group">
                <label>Primary Advertising Goal *</label>
                <select
                  className="form-select"
                  value={form.primary_goal}
                  autoFocus
                  onChange={(e) => set("primary_goal", e.target.value)}
                >
                  {PRIMARY_GOALS.map((g) => (
                    <option key={g.value} value={g.value}>{g.label}</option>
                  ))}
                </select>
                <div style={{ fontSize: "0.75rem", color: "var(--text-muted)", marginTop: "0.3rem" }}>
                  The AI tailors campaign suggestions and CPA benchmarks to this goal.
                </div>
              </div>
            </div>
          )}

          {/* Step 4 — Meta Presence */}
          {step === 4 && (
            <div>
              <div className="form-group">
                <label>Facebook Page ID</label>
                <input
                  type="text"
                  className="form-input"
                  placeholder="e.g. 123456789"
                  value={form.facebook_page_id}
                  autoFocus
                  onChange={(e) => set("facebook_page_id", e.target.value)}
                />
                <div style={{ fontSize: "0.75rem", color: "var(--text-muted)", marginTop: "0.3rem" }}>
                  Enables page stats (followers, activity) and your active Ad Library ads to be fetched
                  during audits. Find it at facebook.com/your-page → About → Page Transparency.
                </div>
              </div>
              <div className="form-group">
                <label>Competitor Page IDs</label>
                <input
                  type="text"
                  className="form-input"
                  placeholder="Comma-separated, e.g. 111222333, 444555666"
                  value={form.competitor_page_ids}
                  onChange={(e) => set("competitor_page_ids", e.target.value)}
                />
                <div style={{ fontSize: "0.75rem", color: "var(--text-muted)", marginTop: "0.3rem" }}>
                  Competitor ads are pulled from the Meta Ad Library and analyzed for creative patterns.
                </div>
              </div>
            </div>
          )}

          {/* Step 5 — Website */}
          {step === 5 && (
            <div>
              <div className="form-group">
                <label>Website URL</label>
                <input
                  type="url"
                  className="form-input"
                  placeholder="https://yourbusiness.com"
                  value={form.website_url}
                  autoFocus
                  onChange={(e) => set("website_url", e.target.value)}
                />
                <div style={{ fontSize: "0.75rem", color: "var(--text-muted)", marginTop: "0.3rem" }}>
                  Scraped during each audit for pricing signals, CTAs, headings, and platform to assess
                  landing page / ad message alignment.
                </div>
              </div>
            </div>
          )}

          {/* Step 6 — Review */}
          {step === 6 && (
            <div>
              <p style={{ color: "var(--text-muted)", fontSize: "0.85rem", marginBottom: "0.75rem" }}>
                Review your business profile before saving. Go back to change anything.
              </p>
              <ReviewTable
                rows={[
                  { label: "Industry",         value: form.industry },
                  { label: "Description",      value: form.description },
                  { label: "Business Notes",   value: form.business_notes },
                  { label: "Target Customer",  value: form.target_customer },
                  { label: "Avg Order Value",  value: form.avg_order_value ? `$${form.avg_order_value}` : "" },
                  { label: "Primary Goal",     value: goalLabel },
                  { label: "Facebook Page ID", value: form.facebook_page_id },
                  { label: "Competitor IDs",   value: form.competitor_page_ids },
                  { label: "Website URL",      value: form.website_url },
                ]}
              />
            </div>
          )}

          {/* Navigation */}
          <div style={{ display: "flex", justifyContent: "space-between", marginTop: "1.5rem" }}>
            <button className="btn" onClick={step === 0 ? onClose : () => setStep((s) => s - 1)}>
              {step === 0 ? "Cancel" : "← Back"}
            </button>

            {step < STEPS.length - 1 ? (
              <button
                className="btn btn-primary"
                onClick={() => setStep((s) => s + 1)}
                disabled={!canProceed()}
              >
                Next →
              </button>
            ) : (
              <button className="btn btn-primary" onClick={handleSave} disabled={saving}>
                {saving ? "Saving…" : "Save Profile"}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Review table helper ──────────────────────────────────────────────────────

function ReviewTable({ rows }: { rows: { label: string; value: string }[] }) {
  return (
    <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.85rem" }}>
      <tbody>
        {rows.map(({ label, value }) => (
          <tr key={label} style={{ borderBottom: "1px solid var(--border, rgba(255,255,255,0.08))" }}>
            <td style={{ padding: "0.45rem 0.5rem", color: "var(--text-muted)", whiteSpace: "nowrap", width: "35%", verticalAlign: "top" }}>
              {label}
            </td>
            <td style={{ padding: "0.45rem 0.5rem", fontWeight: value ? 500 : 400, color: value ? "inherit" : "var(--text-muted)", whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
              {value || <em>Not set</em>}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
