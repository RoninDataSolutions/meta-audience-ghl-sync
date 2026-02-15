import logging
import statistics
from typing import Any

from api.claude_client import normalize_ltv_values

logger = logging.getLogger(__name__)


def normalize_and_stats(
    ltv_values: list[float],
) -> tuple[list[int], dict[str, Any]]:
    """Normalize LTV values via Claude and compute distribution stats.

    Returns:
        (percentiles, stats_dict) where stats_dict contains min/max/median
        and a 10-bucket distribution.
    """
    percentiles = normalize_ltv_values(ltv_values)

    # Compute stats
    stats: dict[str, Any] = {
        "min_ltv": float(min(ltv_values)) if ltv_values else 0,
        "max_ltv": float(max(ltv_values)) if ltv_values else 0,
        "median_ltv": float(statistics.median(ltv_values)) if ltv_values else 0,
        "mean_ltv": float(statistics.mean(ltv_values)) if ltv_values else 0,
        "count": len(ltv_values),
    }

    # 10-bucket distribution (0-10, 10-20, ..., 90-100)
    distribution = [0] * 10
    for p in percentiles:
        bucket = min(p // 10, 9)  # 100 goes into bucket 9
        distribution[bucket] += 1
    stats["distribution"] = distribution

    logger.info(
        f"Normalization stats: min=${stats['min_ltv']:.2f}, "
        f"max=${stats['max_ltv']:.2f}, median=${stats['median_ltv']:.2f}"
    )
    return percentiles, stats
