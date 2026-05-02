export interface SyncConfig {
  id: number;
  ghl_ltv_field_key: string;
  ghl_ltv_field_name: string;
  meta_ad_account_id: string;
  meta_audience_id: string | null;
  meta_lookalike_id: string | null;
  sync_enabled: boolean;
}

export interface CustomField {
  id: string;
  name: string;
  fieldKey?: string;
}

export interface NormalizationStats {
  min_ltv: number;
  max_ltv: number;
  median_ltv: number;
  mean_ltv: number;
  count: number;
  distribution: number[];
}

export interface SyncRun {
  id: number;
  config_id: number;
  started_at: string | null;
  completed_at: string | null;
  status: "running" | "success" | "warning" | "failed";
  contacts_processed: number;
  contacts_matched: number;
  meta_audience_id: string | null;
  meta_audience_name: string | null;
  meta_lookalike_id: string | null;
  meta_lookalike_name: string | null;
  error_message: string | null;
  normalization_stats: NormalizationStats | null;
  duration_seconds: number | null;
}

export interface ContactSample {
  ghl_contact_id: string;
  email: string | null;
  first_name: string | null;
  last_name: string | null;
  raw_ltv: number;
  normalized_value: number;
}

export interface SyncRunDetail extends SyncRun {
  contact_samples: ContactSample[];
}

export interface SyncStatus {
  is_running: boolean;
  running_sync_id: number | null;
  last_run: SyncRun | null;
}

export interface SyncHistory {
  runs: SyncRun[];
  total: number;
  page: number;
  per_page: number;
  total_pages: number;
}

// ── Audit ──────────────────────────────────────────────────────────────────

export interface BusinessProfile {
  industry?: string;
  description?: string;
  target_customer?: string;
  avg_order_value?: number;
  primary_goal?: string;
  facebook_page_id?: string;
  competitor_page_ids?: string;
}

export interface AdAccount {
  id: number;
  account_id: string;
  account_name: string;
  has_custom_token: boolean;
  notification_email: string | null;
  audit_cron: string | null;
  is_active: boolean;
  last_audit_at: string | null;
  currency: string | null;
  timezone_name: string | null;
  website_url: string | null;
  business_profile: BusinessProfile;
  created_at: string | null;
}

export interface AuditDelta {
  previous: number;
  current: number;
  change_pct: number;
}

export interface AuditReport {
  id: number;
  account_id: string;
  account_name?: string;
  generated_at: string | null;
  status: "in_progress" | "completed" | "failed";
  total_spend_7d: number | null;
  total_spend_30d: number | null;
  total_conversions_7d: number | null;
  total_conversions_30d: number | null;
  total_impressions_7d: number | null;
  total_impressions_30d: number | null;
  total_clicks_7d: number | null;
  total_clicks_30d: number | null;
  avg_cpa_30d: number | null;
  avg_ctr_30d: number | null;
  avg_roas_30d: number | null;
  campaign_count: number | null;
  audience_count: number | null;
  models_used: string | null;
  has_pdf: boolean;
  error_message: string | null;
}

export interface AuditReportDetail extends AuditReport {
  analyses: Record<string, any>;
  raw_metrics: Record<string, any> | null;
  comparison: {
    previous_report_id: number;
    previous_generated_at: string;
    deltas: Record<string, AuditDelta | null>;
  } | null;
}
