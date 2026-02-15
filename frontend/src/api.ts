import type {
  SyncConfig,
  CustomField,
  SyncStatus,
  SyncHistory,
  SyncRunDetail,
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
