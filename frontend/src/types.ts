export interface SyncConfig {
  id: number;
  ghl_ltv_field_key: string;
  ghl_ltv_field_name: string;
  meta_ad_account_id: string;
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
