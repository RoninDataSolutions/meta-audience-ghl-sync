import bisect
import logging

logger = logging.getLogger(__name__)


def normalize_ltv_values(ltv_values: list[float]) -> list[int]:
    """Compute percentile ranks (0-100) for LTV values.

    Uses local sort-based percentile ranking — deterministic and exact.
    Each value receives its rank relative to the full distribution:
    the minimum gets 0, the maximum gets 100, ties get the same rank.
    """
    if not ltv_values:
        return []

    if len(ltv_values) == 1:
        return [50]

    sorted_vals = sorted(ltv_values)
    n = len(sorted_vals)

    result = []
    for v in ltv_values:
        rank = bisect.bisect_left(sorted_vals, v)
        percentile = int(rank / (n - 1) * 100)
        result.append(percentile)

    logger.info(f"Normalization complete: {len(result)} values processed")
    return result
