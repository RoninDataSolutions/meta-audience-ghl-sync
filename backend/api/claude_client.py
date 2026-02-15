import json
import logging
import os
import re

import anthropic

from config import settings

logger = logging.getLogger(__name__)

CHUNK_SIZE = 500
client = anthropic.Anthropic(api_key=settings.CLAUDE_API_KEY)

PROMPT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompts")


def _load_prompt(name: str) -> str:
    """Load a prompt template from the prompts/ directory."""
    path = os.path.join(PROMPT_DIR, f"{name}.txt")
    with open(path, "r") as f:
        return f.read().strip()


def _parse_json_array(text: str) -> list[int]:
    """Parse a JSON array from Claude's response, stripping markdown fences if present."""
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    cleaned = cleaned.strip()
    result = json.loads(cleaned)
    if not isinstance(result, list):
        raise ValueError(f"Expected JSON array, got {type(result)}")
    return [int(v) for v in result]


def normalize_ltv_values(ltv_values: list[float]) -> list[int]:
    """Send LTV values to Claude for percentile normalization. Returns 0-100 integers."""
    if not ltv_values:
        return []

    if len(ltv_values) == 1:
        return [50]

    all_percentiles: list[int] = []

    for i in range(0, len(ltv_values), CHUNK_SIZE):
        chunk = ltv_values[i:i + CHUNK_SIZE]
        chunk_idx = (i // CHUNK_SIZE) + 1
        total_chunks = (len(ltv_values) + CHUNK_SIZE - 1) // CHUNK_SIZE

        logger.info(f"Normalizing chunk {chunk_idx}/{total_chunks} ({len(chunk)} values)")

        min_val = min(chunk)
        max_val = max(chunk)

        template = _load_prompt("normalize_ltv")
        prompt = template.format(
            count=len(chunk),
            min_val=f"{min_val:.2f}",
            max_val=f"{max_val:.2f}",
            values=json.dumps(chunk),
        )

        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )

        response_text = message.content[0].text
        percentiles = _parse_json_array(response_text)

        if len(percentiles) != len(chunk):
            raise ValueError(
                f"Claude returned {len(percentiles)} values but expected {len(chunk)}"
            )

        # Clamp to 0-100
        percentiles = [max(0, min(100, p)) for p in percentiles]
        all_percentiles.extend(percentiles)

    logger.info(f"Normalization complete: {len(all_percentiles)} values processed")
    return all_percentiles
