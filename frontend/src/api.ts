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

export async function getConfig(): Promise<{
  config: SyncConfig | null;
  meta_ad_account_id: string;
  ghl_location_name: string;
  smtp_from: string;
  smtp_to: string;
}> {
  return request("/api/config");
}

export async function saveConfig(payload: {
  ghl_ltv_field_key: string;
  ghl_ltv_field_name: string;
  meta_audience_id?: string;
  meta_lookalike_id?: string;
}): Promise<{ config: SyncConfig }> {
  return request("/api/config", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function triggerSync(): Promise<{
  message: string;
  config_id: number;
}> {
  return request("/api/sync/trigger", { method: "POST" });
}

export async function getSyncStatus(): Promise<SyncStatus> {
  return request("/api/sync/status");
}

export async function getSyncHistory(
  page: number = 1
): Promise<SyncHistory> {
  return request(`/api/sync/history?page=${page}`);
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

// ── Audit ───────────────────────────────────────────────────────────────────

export async function triggerAudit(payload: {
  account_id?: string;
  models?: string[];
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
