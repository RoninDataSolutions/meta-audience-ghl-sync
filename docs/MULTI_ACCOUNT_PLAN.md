# Multi-Account Credential Architecture

## Goal

Replace the env-based single-account setup with a proper multi-account system where each client has their own set of credentials, stored securely in AWS Secrets Manager, managed through the existing Accounts UI, and fully portable from local Docker to Render.

---

## Decisions

| Question | Answer |
|---|---|
| Secret storage | AWS Secrets Manager |
| Infrastructure | Local Docker (Unraid) → Render migration path |
| GHL ↔ Meta relationship | 1:1 per account |
| Stripe | Each client has their own Stripe account |
| Who manages accounts | Admin only (you) |

---

## What Lives Where After Migration

### Stays in `.env` (global, infrastructure-level)
```
# AWS (used to talk to Secrets Manager)
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...

# Database
POSTGRES_HOST / PORT / DB / USER / PASSWORD

# Global email (SMTP)
SMTP_HOST / PORT / USERNAME / PASSWORD / FROM / TO

# AI
CLAUDE_API_KEY=...

# App
WEB_PORT=9876
LOG_LEVEL=INFO
SYNC_SCHEDULE_CRON=...
```

### Moves to AWS Secrets Manager (per account)
Each account gets one secret in SM at path `/ghl-sync/accounts/{account_id}` containing a JSON bundle:

```json
{
  "meta_access_token": "EAAM...",
  "meta_capi_dataset_id": "1084399...",
  "meta_capi_access_token": "EAAM...",
  "ghl_api_key": "pit-...",
  "ghl_location_id": "sPQk9...",
  "ghl_location_name": "YogiSoul",
  "stripe_secret_key": "sk_live_...",
  "stripe_webhook_secret": "whsec_...",
  "capi_event_source_url": "https://yoga.ronindatasolutions.com",
  "capi_event_name": "Purchase"
}
```

One API call fetches the whole bundle. SM caches secrets in the SDK for 60 seconds by default — no per-request latency after the first call.

---

## Database Changes

### `AdAccount` table — new columns
```
aws_secret_name   VARCHAR   — SM secret name, e.g. "/ghl-sync/accounts/89313216"
```

The DB row stores the **secret name**, never the credential values. Existing `meta_access_token` column on `AdAccount` gets dropped (or kept for the transition period with a `migrated` flag).

### `SyncConfig` — no changes
Already has `meta_ad_account_id` as the join key. The service layer resolves credentials from `AdAccount.aws_secret_name`.

---

## New Service: `CredentialResolver`

```
backend/services/credential_resolver.py
```

Single responsibility: given an `account_id`, return a `AccountCredentials` dataclass with all per-account fields. Internally:

1. Looks up `AdAccount` by `account_id`, gets `aws_secret_name`
2. Calls AWS SM `get_secret_value(SecretId=aws_secret_name)`
3. Parses JSON → returns `AccountCredentials`
4. **Fallback**: if `account_id` is the default account and no SM secret exists, reads from `settings` (env). This keeps backward compatibility during migration and lets the app still work if SM is unreachable.

All existing services (`sync_service`, `conversion_tracker`, `meta_audit`, `ghl_client`, `meta_client`) stop reading from `settings` for per-account fields and instead receive an `AccountCredentials` object passed in from the caller.

---

## Migration: YogiSoul from `.env` → Secrets Manager

A one-time migration script `backend/scripts/migrate_default_account.py`:

1. Reads current env values (`META_ACCESS_TOKEN`, `GHL_API_KEY`, etc.)
2. Creates or updates AWS SM secret `/ghl-sync/accounts/89313216`
3. Upserts the `AdAccount` row for `89313216` with `aws_secret_name` set
4. Prints a checklist of env vars that are now safe to remove

After running: the env vars for YogiSoul get removed from `.env`. The app reads them from SM via the resolver.

---

## Frontend Changes (Accounts Page)

The account creation/edit form gains credential fields. Each field:
- Input is `type="password"` (masked)
- On load, shows ✓ or ✗ per field (fetched from a `/api/accounts/{id}/credential-status` endpoint that checks SM without returning values)
- Leave blank = keep existing secret unchanged (SM partial update)
- "Test" button per section (Meta, GHL, Stripe) calls the existing test endpoints with the stored credentials

New fields in the form:
```
── Meta ──────────────────────────────────────
Meta Access Token          [password input]
CAPI Dataset ID            [text input]
CAPI Access Token          [password input]
CAPI Event Source URL      [text input]

── GHL ───────────────────────────────────────
GHL API Key                [password input]
GHL Location ID            [text input]
GHL Location Name          [text input]

── Stripe ────────────────────────────────────
Stripe Secret Key          [password input]
Stripe Webhook Secret      [password input]
```

---

## Render Migration Path

Render is fully compatible with this design. The only change needed to move from Unraid to Render:

1. Set `AWS_REGION`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` as Render env vars (or use an IAM role if hosting on EC2/ECS)
2. Set `POSTGRES_*` pointing at the new DB (Render Postgres or external)
3. Set `SMTP_*`, `CLAUDE_API_KEY`
4. Deploy — all account credentials come from SM, nothing else changes

No per-account env vars exist on Render. Adding a new client = create an SM secret + add an `AdAccount` row through the UI. No deploy required.

---

## Phases

### Phase 1 — Secrets Manager integration
- Add `boto3` to `requirements.txt`
- Add `AWS_REGION / AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY` to `.env` (already have AWS CLI access so creds exist)
- Write `CredentialResolver` with SM fetch + env fallback
- Add `aws_secret_name` column to `AdAccount`
- DB migration SQL

### Phase 2 — Refactor services to use resolver
- `sync_service.py` — pass `AccountCredentials` instead of reading `settings`
- `meta_audit.py` — same
- `conversion_tracker.py` — same
- `ghl_client.py` / `meta_client.py` — accept credentials as params, not module-level globals
- All routers pass credentials through from the resolved account

### Phase 3 — Migrate YogiSoul
- Run `migrate_default_account.py`
- Verify app works end-to-end reading from SM
- Remove per-account vars from `.env`
- Update `.env.example` to reflect the new minimal env shape

### Phase 4 — Frontend credential management
- Expand account form with credential fields
- Add `/api/accounts/{id}/credential-status` endpoint
- Add `/api/accounts/{id}/credentials` PUT endpoint (writes to SM)
- Test-connection buttons per service section

---

## Security Notes

- SM secrets are never logged, never returned in API responses, never stored in DB
- The `CredentialResolver` is the only code path that calls SM — credentials don't flow through HTTP
- `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` in `.env` should be scoped to an IAM policy with `secretsmanager:GetSecretValue` and `secretsmanager:PutSecretValue` on `/ghl-sync/*` only — no broader AWS access
- On Render, use Render's secret env var storage (values are masked in the dashboard) for the AWS credentials
- SM costs: ~$0.40/secret/month + $0.05 per 10k API calls. At 10 accounts that's ~$4/month in secret storage.

---

## What Does NOT Change

- Postgres schema for `SyncRun`, `MatchedConversion`, `AuditReport`, etc. — no changes
- The account selector in the nav — already account-scoped
- The audit, sync, conversion logic — same behavior, just different credential source
- Docker / Dockerfile — no changes
- The `CLAUDE_API_KEY` and `SMTP_*` stay global (not per-account) since the AI and email infrastructure is shared
