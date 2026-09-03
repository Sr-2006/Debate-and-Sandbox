import math
from typing import Tuple
from Arse_shadow.shadow_sandbox.persistence import get_capability_stats


def _beta_ppf_05(a: float, b: float) -> float:
    """Calculates approximate 5th percentile lower bound for Beta(a, b)."""
    try:
        from scipy.stats import beta
        return float(beta.ppf(0.05, a, b))
    except ImportError:
        # Fallback Beta lower bound formula: mean - 1.645 * std_dev
        mean = a / (a + b)
        var = (a * b) / ((a + b) ** 2 * (a + b + 1))
        std = math.sqrt(var)
        return max(0.0, min(1.0, mean - 1.645 * std))


def get_bayesian_prior(capability: str, target_kind: str) -> Tuple[float, int, int, int]:
    """
    Queries execution history for (capability, target_kind).
    Returns (beta_lower_bound_5th_percentile, sample_size, successes, failures).
    """
    stats = get_capability_stats(capability, target_kind)
    successes = stats.get("successes", 0)
    total_runs = stats.get("total_runs", 0)
    failures = total_runs - successes

    # Beta(1 + successes, 1 + failures)
    a = 1.0 + successes
    b = 1.0 + max(0, failures)

    lower_bound = _beta_ppf_05(a, b)
    return lower_bound, total_runs, successes, failures
