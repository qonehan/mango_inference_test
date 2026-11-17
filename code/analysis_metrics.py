"""Pure calculations for protocol v0.9.1. No network or credentials."""
import math
import random


def _cdf(k, n, p):
    if p == 0:
        return 1.0
    if p == 1:
        return float(k >= n)
    return min(1.0, sum(math.exp(math.lgamma(n+1)-math.lgamma(j+1)
        - math.lgamma(n-j+1)+j*math.log(p)+(n-j)*math.log1p(-p))
        for j in range(k+1)))


def _solve_cdf(k, n, target):
    lo, hi = 0.0, 1.0
    for _ in range(80):
        mid = (lo+hi)/2
        if _cdf(k, n, mid) > target:
            lo = mid
        else:
            hi = mid
    return (lo+hi)/2


def exact_binomial_interval(successes, total, alpha):
    """Two-sided Clopper-Pearson, including the zero-failure boundary."""
    if not (isinstance(total, int) and isinstance(successes, int)
            and total > 0 and 0 <= successes <= total and 0 < alpha < 1):
        raise ValueError('invalid binomial sample')
    return (0.0 if successes == 0 else _solve_cdf(successes-1, total, 1-alpha/2),
            1.0 if successes == total else _solve_cdf(successes, total, alpha/2))


def success_difference(groups, alpha=0.05/16):
    """groups: 3 lists of paired binary outcomes, one for each W1/W3/W2.

    Conservative simultaneous marginal exact intervals; pairing need not be
    independent within each pair. Between-observation independence is assumed.
    Returns fractions (multiply by 100 for percentage points).
    """
    if len(groups) != 3 or any(not g for g in groups):
        raise ValueError('all three workloads must be observed')
    point = low = high = 0.0
    for group in groups:
        if any(a not in (0, 1) or b not in (0, 1) for a,b in group):
            raise ValueError('outcomes must be binary')
        n = len(group)
        a, b = sum(x[0] for x in group), sum(x[1] for x in group)
        la, ua = exact_binomial_interval(a,n,alpha/6)
        lb, ub = exact_binomial_interval(b,n,alpha/6)
        point += (a-b)/n/3
        low += (la-ub)/3
        high += (ua-lb)/3
    return point, low, high


def cost_ratio(pairs):
    """Each pair is ((M billed cost, M success), (OR billed cost, OR success)).

    Failed sessions are retained. None means unidentified billing or denominator.
    """
    if not pairs:
        return None
    if any(c is None for pair in pairs for c,s in pair):
        return None
    if any(not math.isfinite(c) or c < 0 for pair in pairs for c,s in pair):
        raise ValueError('billing must be finite and nonnegative')
    totals = [sum(p[a][0] for p in pairs) for a in (0,1)]
    counts = [sum(bool(p[a][1]) for p in pairs) for a in (0,1)]
    if not all(counts) or totals[1] == 0:
        return None
    return (totals[0]/counts[0])/(totals[1]/counts[1])


def decode_rate(completion_tokens, reasoning_tokens, reasoning_seen, times):
    """times contains only chunks with generated reasoning/content/tool data.

    Missing reasoning accounting is not silently treated as zero. This is an
    approximation because native token count in the first chunk is unavailable.
    """
    if completion_tokens is None or reasoning_tokens is None:
        return None
    if reasoning_tokens > 0 and not reasoning_seen:
        return None
    if len(times) < 2 or completion_tokens <= 1 or times[-1] <= times[0]:
        return None
    return (completion_tokens-1)/(times[-1]-times[0])


def stratified_interval(strata, statistic, alpha=0.05, repeats=10000, seed=20261116):
    """Resample whole rows/pairs within strata. Undefined replicates invalidate CI."""
    if not strata or any(not s for s in strata):
        raise ValueError('empty stratum')
    rng = random.Random(seed)
    values=[]
    for _ in range(repeats):
        rows=[rng.choice(s) for s in strata for _ in range(len(s))]
        value=statistic(rows)
        if value is None or not math.isfinite(value):
            return None
        values.append(value)
    values.sort()
    def quantile(q):
        x=q*(len(values)-1)
        i=int(x)
        return values[i]+(values[min(i+1,len(values)-1)]-values[i])*(x-i)
    return quantile(alpha/2), quantile(1-alpha/2)


def token_share(rows, provider):
    """Rows (final_provider, input_plus_output_tokens); confirmed rows only."""
    if any(t is None or not math.isfinite(t) or t < 0 for p,t in rows):
        raise ValueError('missing/invalid tokens must be reported separately')
    total=sum(t for p,t in rows)
    return sum(t for p,t in rows if p == provider)/total if total else None
