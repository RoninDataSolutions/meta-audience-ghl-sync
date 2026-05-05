import type {
  SyncConfig,
  CustomField,
  SyncStatus,
  SyncHistory,
  SyncRunDetail,
  AdAccount,
  AuditReport,
  AuditReportDetail,
} from "./types";

const BASE = "";

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const resp = await fetch(`${BASE}${url}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: resp.statusText }));
    throw new Error(err.detail || resp.statusText);
  }
  return resp.json();
}

export async function getCustomFields(): Promise<CustomField[]> {
  const data = await request<{ customFields: CustomField[] }>(
    "/api/custom-fields"
  );
  return data.customFields;
}

export async function getConfig(accountId?: string): Promise<{
  config: SyncConfig | null;
  meta_ad_account_id: string;
  ghl_location_name: string;
  smtp_from: string;
  smtp_to: string;
}> {
  const qs = accountId ? `?account_id=${accountId}` : "";
  return request(`/api/config${qs}`);
}

export async function saveConfig(
  payload: {
    ghl_ltv_field_key: string;
    ghl_ltv_field_name: string;
    meta_audience_id?: string;
    meta_lookalike_id?: string;
  },
  accountId?: string
): Promise<{ config: SyncConfig }> {
  const qs = accountId ? `?account_id=${accountId}` : "";
  return request(`/api/config${qs}`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function triggerSync(accountId?: string): Promise<{
  message: string;
  config_id: number;
}> {
  const qs = accountId ? `?account_id=${accountId}` : "";
  return request(`/api/sync/trigger${qs}`, { method: "POST" });
}

export async function getSyncStatus(accountId?: string): Promise<SyncStatus> {
  const qs = accountId ? `?account_id=${accountId}` : "";
  return request(`/api/sync/status${qs}`);
}

export async function getSyncHistory(
  page: number = 1,
  accountId?: string
): Promise<SyncHistory> {
  const qs = new URLSearchParams({ page: String(page) });
  if (accountId) qs.set("account_id", accountId);
  return request(`/api/sync/history?${qs}`);
}

export async function getSyncDetail(id: number): Promise<SyncRunDetail> {
  return request(`/api/sync/${id}`);
}

export async function sendTestEmail(): Promise<{
  success: boolean;
  message: string;
}> {
  return request("/api/email/test", { method: "POST" });
}

// ── Ad Accounts ─────────────────────────────────────────────────────────────

export async function getAccounts(): Promise<{ accounts: AdAccount[] }> {
  return request("/api/accounts");
}

export async function createAccount(payload: {
  account_id: string;
  account_name: string;
  meta_access_token?: string;
  notification_email?: string;
  audit_cron?: string;
  website_url?: string;
  business_profile?: Record<string, unknown>;
}): Promise<AdAccount> {
  return request("/api/accounts", { method: "POST", body: JSON.stringify(payload) });
}

export async function updateAccount(
  id: number,
  payload: {
    account_name?: string;
    meta_access_token?: string;
    notification_email?: string;
    audit_cron?: string;
    is_active?: boolean;
    website_url?: string;
    business_profile?: Record<string, unknown>;
    business_notes?: string;
  }
): Promise<AdAccount> {
  return request(`/api/accounts/${id}`, { method: "PUT", body: JSON.stringify(payload) });
}

export async function deactivateAccount(id: number): Promise<void> {
  return request(`/api/accounts/${id}`, { method: "DELETE" });
}

export async function testAccountToken(
  id: number
): Promise<{ status: string; account_name?: string; detail?: string }> {
  return request(`/api/accounts/${id}/test`, { method: "POST" });
}

export async function getCredentialStatus(id: number): Promise<{
  has_sm_credentials: boolean;
  secret_name?: string;
  keys: Record<string, boolean>;
}> {
  return request(`/api/accounts/${id}/credential-status`);
}

export async function saveCredentials(
  id: number,
  payload: Record<string, string>
): Promise<{ status: string; secret_name: string; arn: string }> {
  return request(`/api/accounts/${id}/credentials`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

// ── Audit ───────────────────────────────────────────────────────────────────

export async function triggerAudit(payload: {
  account_id?: string;
  models?: string[];
  report_notes?: string;
}): Promise<{ status: string; report_id: number; account_name: string }> {
  return request("/api/audit/trigger", { method: "POST", body: JSON.stringify(payload) });
}

export async function getAuditReports(params?: {
  limit?: number;
  offset?: number;
  account_id?: string;
}): Promise<{ reports: AuditReport[]; total: number }> {
  const qs = new URLSearchParams();
  if (params?.limit) qs.set("limit", String(params.limit));
  if (params?.offset) qs.set("offset", String(params.offset));
  if (params?.account_id) qs.set("account_id", params.account_id);
  return request(`/api/audit/reports?${qs}`);
}

export async function getAuditReport(id: number): Promise<AuditReportDetail> {
  return request(`/api/audit/reports/${id}`);
}

export async function deleteAuditReport(id: number): Promise<void> {
  return request(`/api/audit/reports/${id}`, { method: "DELETE" });
}

export async function reanalyzeAudit(
  id: number,
  payload: { models?: string[]; context_text?: string }
): Promise<{ status: string; report_id: number; models: string[]; message: string }> {
  return request(`/api/audit/reports/${id}/reanalyze`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function addAuditContext(
  id: number,
  text: string
): Promise<{ audit_contexts: Array<{ text: string; added_at: string }> }> {
  return request(`/api/audit/reports/${id}/context`, {
    method: "POST",
    body: JSON.stringify({ text }),
  });
}

// ── Conversions ──────────────────────────────────────────────────────────────

import type { Conversion, ConversionStats } from "./types";

export async function getConversions(params?: {
  limit?: number;
  offset?: number;
  status?: string;
  source?: string;
}): Promise<{ conversions: Conversion[]; total: number; stats: ConversionStats }> {
  const qs = new URLSearchParams();
  if (params?.limit)  qs.set("limit",  String(params.limit));
  if (params?.offset) qs.set("offset", String(params.offset));
  if (params?.status) qs.set("status", params.status);
  if (params?.source) qs.set("source", params.source);
  return request(`/api/conversions?${qs}`);
}

export async function getConversionDetail(id: number): Promise<Conversion> {
  return request(`/api/conversions/${id}`);
}

export async function retryConversion(id: number): Promise<{ status: string; id: number }> {
  return request(`/api/conversions/${id}/retry`, { method: "POST" });
}
