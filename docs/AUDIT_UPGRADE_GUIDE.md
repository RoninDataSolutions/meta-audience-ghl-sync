# Upgrading the Audit Engine — Opus Model + Analysis Quality

This guide covers two changes to `meta_audit.py`:

1. **Add Claude Opus 4.6 as a selectable model** — deeper reasoning, better business-context integration
2. **Fix the analysis logic** — stop defaulting to lead gen recommendations for DTC businesses

The `ghl_client.py` file doesn't need any changes.

---

## 1. Add the Opus model

### File: `meta_audit.py`

#### 1a. Add `analyze_with_claude_opus` function

Add this directly below the existing `analyze_with_claude` function (after line 957):

```python
async def analyze_with_claude_opus(payload_str: str, api_key: str) -> dict:
    """POST to Anthropic API (claude-opus-4-6). Deeper reasoning model for strategic analysis."""
    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-opus-4-6-20250515",
                    "max_tokens": 16000,
                    "system": AUDIT_SYSTEM_PROMPT,
                    "messages": [{"role": "user", "content": payload_str}],
                },
            )
            resp.raise_for_status()
            data = resp.json()
            raw_text = data["content"][0]["text"]
            cleaned = _strip_markdown_fences(raw_text)
            return json.loads(cleaned)
    except Exception as e:
        logger.error(f"Claude Opus analysis failed: {e}")
        return {"error": str(e)}
```

Key differences from the Sonnet function:
- `model`: `claude-opus-4-6-20250515`
- `timeout`: 300 seconds (Opus is slower — Sonnet's 120s will cause timeouts)
- `max_tokens`: 16000 (Opus produces more thorough analysis — give it room)

#### 1b. Register Opus in `run_audit` and `reanalyze_audit`

In `run_audit`, find the block that dispatches analysis tasks (around line 1519). Add the Opus case:

```python
        if "claude" in models_to_run:
            analysis_tasks.append(analyze_with_claude(payload_str, settings.CLAUDE_API_KEY))
            analysis_labels.append("claude")

        # ADD THIS BLOCK:
        if "claude_opus" in models_to_run:
            analysis_tasks.append(analyze_with_claude_opus(payload_str, settings.CLAUDE_API_KEY))
            analysis_labels.append("claude_opus")

        if "openai" in models_to_run:
            openai_key = getattr(settings, "OPENAI_API_KEY", "")
            analysis_tasks.append(analyze_with_openai(payload_str, openai_key))
            analysis_labels.append("openai")
```

Do the same in `reanalyze_audit` (around line 1726):

```python
        if "claude" in models_to_run:
            analysis_tasks.append(analyze_with_claude(payload_str, settings.CLAUDE_API_KEY))
            analysis_labels.append("claude")

        # ADD THIS BLOCK:
        if "claude_opus" in models_to_run:
            analysis_tasks.append(analyze_with_claude_opus(payload_str, settings.CLAUDE_API_KEY))
            analysis_labels.append("claude_opus")

        if "openai" in models_to_run:
            openai_key = getattr(settings, "OPENAI_API_KEY", "")
            analysis_tasks.append(analyze_with_openai(payload_str, openai_key))
            analysis_labels.append("openai")
```

#### 1c. No new API key needed

Opus uses the same `CLAUDE_API_KEY` / `settings.CLAUDE_API_KEY` as Sonnet. Same endpoint, same auth. The only difference is the model string and the cost per token (Opus is ~5x more expensive than Sonnet).

---

### File: `audit_pdf.py`

Add a badge color for Opus in whatever dict maps model names to colors:

```python
MODEL_COLORS = {
    "claude": "#d97706",        # Amber
    "claude_opus": "#7c3aed",   # Purple
    "openai": "#10a37f",        # Green
}

MODEL_LABELS = {
    "claude": "CLAUDE SONNET",
    "claude_opus": "CLAUDE OPUS",
    "openai": "GPT-4o",
}
```

The PDF renderer should already handle any model key in the `analyses` dict. If it iterates over `analyses.items()`, Opus will render as a third tab/section with no code changes beyond the color/label mapping.

---

### Frontend

Add a third checkbox to the model selector in `AuditTrigger.tsx`:

```
☑ Claude Sonnet (fast, default)
☐ Claude Opus (deep analysis, slower)
☐ GPT-4o (second opinion)
```

The trigger endpoint already accepts `models: ["claude", "claude_opus", "openai"]` in the request body — no backend route changes needed.

---

## 2. Fix the analysis quality

The system prompt is already excellent — it has business context, conversation insights, LTV insights, all the enrichment. The problem isn't the prompt structure, it's that the prompt doesn't give the model enough guidance on **what type of campaign to recommend based on the business model**.

### File: `meta_audit.py` — Update `AUDIT_SYSTEM_PROMPT`

Find this paragraph in the system prompt (around line 95-96):

```
When business_context is present, integrate it throughout your analysis — don't summarize it separately. Cite actual prices, CTAs, page follower counts, competitor observations, and business model specifics inline. The business_notes and report_context fields in particular should visibly shape your campaign recommendations, CPA benchmarks, audience strategy, and projections.
```

Replace it with:

```
When business_context is present, integrate it throughout your analysis — don't summarize it separately. Cite actual prices, CTAs, page follower counts, competitor observations, and business model specifics inline. The business_notes and report_context fields in particular should visibly shape your campaign recommendations, CPA benchmarks, audience strategy, and projections.

CRITICAL — Campaign Objective Recommendations:
Do NOT default to OUTCOME_LEADS with Meta Lead Forms for every business. Match the campaign objective to the actual business model:

- **DTC / E-commerce / Online services with a website where customers can buy directly** (online yoga, SaaS, courses, e-commerce stores): Recommend OUTCOME_SALES or website conversion campaigns optimized for the Purchase/BookClass/SignUp event on the business website. The customer should land on the website and complete the transaction there. Only recommend lead forms if there is NO website or NO pixel tracking set up — and if so, flag "set up Meta Pixel + Conversions API" as the #1 priority action.

- **High-ticket B2B / services requiring consultation** (agencies, consultants, contractors with $1K+ deal sizes): OUTCOME_LEADS with lead forms or landing page conversions is appropriate here because the sales cycle requires human follow-up.

- **Local brick-and-mortar businesses** (restaurants, gyms, salons): OUTCOME_TRAFFIC driving to Google Maps/booking page, or OUTCOME_LEADS for appointment booking forms.

- **Messaging-first businesses** (businesses that close deals through Instagram DMs or WhatsApp — check conversation_insights for volume): MESSAGES objective with conversation-optimized ads. If conversation_insights shows high messaging volume but low conversion to purchase, recommend adding a direct conversion campaign alongside the messaging campaign, not replacing it.

- **If the business has an active website** (check the "website" field in business_context): Always consider website conversion campaigns first. Look at what the website offers — if it has pricing pages, booking flows, or checkout, that's where the conversion should happen.

- **If the business is running OUTCOME_ENGAGEMENT but is a transactional business**: This is almost always wrong. Flag it as a critical misalignment. Engagement campaigns optimize for likes/comments/shares — they do NOT optimize for buyers. The "conversions" from an engagement campaign are post engagements, not business conversions. Call this out explicitly and recommend switching.

When making campaign recommendations in the action_plan.campaigns_to_create section, always specify WHY you chose that objective for this specific business type. Don't just say "OUTCOME_LEADS" — say "OUTCOME_LEADS because this is a high-ticket service requiring consultation calls" or "OUTCOME_SALES with website purchase optimization because this business sells $179 packages directly on their website."
```

### Why this fixes the problem

The current prompt tells the model to "analyze whatever is present" but doesn't tell it how to match campaign objectives to business models. Sonnet (and most models) default to OUTCOME_LEADS because it's the most common recommendation in digital marketing content. By explicitly listing the decision tree — DTC → website conversions, B2B → lead forms, messaging-first → messages objective — the model makes the right recommendation even without Opus-level reasoning.

This is the change that turned Sonnet's YogiSoul analysis from "decent, switch to lead gen" to "critical misalignment, 0.06% conversion rate, catastrophic $1,661 CPL."

---

## 3. Additional system prompt improvements

While you're in the system prompt, add this paragraph after the campaign objective section above. It addresses the other issue from the latest report — recommending "pause immediately" when there's nothing else running:

```
CRITICAL — Tactical Recommendations:
- **Never recommend pausing the only running campaign without a replacement ready.** If the account has a single active campaign, recommend launching the new campaign first, letting it exit Meta's learning phase (50+ optimization events), THEN scaling down the old campaign. Going dark for 1-2 weeks while a new campaign ramps up is worse than running a mediocre campaign.
- **Budget recommendations must be grounded in what the client currently spends.** If current daily spend is $20/day, don't recommend $55/day without explicitly calling out that this is a 2.75x budget increase and explaining why the unit economics support it (e.g., "with $179 LTV and projected $60 CPA, every $60 spent returns $179 — a 3x return justifies doubling the budget").
- **Don't recommend more than 2 new campaigns.** A small business with one campaign running $20/day cannot manage 3-4 new campaigns simultaneously. Be realistic about operational capacity.
```

---

## 4. Rename "claude" label to "claude_sonnet" (optional but recommended)

Right now the Sonnet model is labeled `"claude"` throughout the codebase. With Opus added, it's clearer to rename it to `"claude_sonnet"` so the frontend and PDF can distinguish them. This is optional — you can ship with `"claude"` for Sonnet and `"claude_opus"` for Opus and it'll work fine. But if you do rename:

- `analyze_with_claude` → no function rename needed, just change the label
- In `run_audit` and `reanalyze_audit`: `"claude"` stays as the accepted model key (backward compatible with existing reports)
- In the PDF and frontend, display "Claude Sonnet" for `"claude"` and "Claude Opus" for `"claude_opus"`

---

## 5. Summary of changes

| File | Change | Lines |
|---|---|---|
| `meta_audit.py` | Add `analyze_with_claude_opus()` function | ~20 lines, after line 957 |
| `meta_audit.py` | Register `"claude_opus"` in `run_audit` dispatch | 3 lines, around line 1519 |
| `meta_audit.py` | Register `"claude_opus"` in `reanalyze_audit` dispatch | 3 lines, around line 1726 |
| `meta_audit.py` | Update `AUDIT_SYSTEM_PROMPT` with campaign objective guidance | ~30 lines, replace text at line 95-96 |
| `meta_audit.py` | Update `AUDIT_SYSTEM_PROMPT` with tactical recommendation guardrails | ~10 lines, add after campaign objective section |
| `audit_pdf.py` | Add Opus badge color + label to model mapping | 2 lines |
| Frontend | Add Opus checkbox to model selector | ~5 lines in `AuditTrigger.tsx` |

No database migration needed. No new environment variables. No new dependencies. The existing `CLAUDE_API_KEY` works for both Sonnet and Opus.

---

## 6. Usage recommendations

**Default workflow:** Run Sonnet (`claude`) on every audit. It's fast (~30s), cheap, and with the updated system prompt, it'll give much better recommendations.

**Deep analysis:** Run Opus (`claude_opus`) when you want strategic-level reasoning — new client onboarding, quarterly reviews, or when Sonnet's recommendations don't feel right. Opus is ~5x more expensive and ~3x slower but catches business model nuances that Sonnet misses.

**Multi-model:** Run both Sonnet and Opus on the same data. The PDF and frontend already support multiple model tabs. Where they agree, you have high confidence. Where they disagree, that tension highlights the genuinely ambiguous decisions.

**Best of both worlds:** Run Sonnet weekly (automated via cron), run Opus monthly (manual trigger for the deep review).
