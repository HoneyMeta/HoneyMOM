"""Measurement System Analysis (MSA) calculations.

Implements the methods of the AIAG MSA Reference Manual, 4th edition (2010):

* ``grr_xbar_r``           - Gauge R&R, Average & Range method
* ``grr_anova``            - Gauge R&R, two-way crossed ANOVA (with interaction pooling)
* ``bias``                 - independent-sample bias study
* ``linearity``            - linearity study (per-reading bias regression)
* ``attribute_agreement``  - attribute agreement analysis (kappa, effectiveness, miss/false alarm)
* ``report_rows``          - flatten any result into [(label, value)] rows for display

All functions return plain dicts of Python floats/ints/lists at full precision; round only
for presentation.  numpy is required; scipy is optional (used for distribution functions when
available, otherwise built-in incomplete-beta routines give the same values).
"""
from __future__ import annotations

import math
from typing import Any, Iterable, Sequence

import numpy as np

__all__ = [
    "grr_xbar_r",
    "grr_anova",
    "bias",
    "linearity",
    "attribute_agreement",
    "report_rows",
    "d2",
    "d2_star",
]

# ---------------------------------------------------------------------------
# AIAG constants
# ---------------------------------------------------------------------------

# K1 = 1/d2 (number of ranges large), indexed by number of trials
K1_TABLE = {2: 0.8862, 3: 0.5908}
# K2 = 1/d2*(m, g=1), indexed by number of appraisers
K2_TABLE = {2: 0.7071, 3: 0.5231}
# K3 = 1/d2*(m, g=1), indexed by number of parts
K3_TABLE = {2: 0.7071, 3: 0.5231, 4: 0.4467, 5: 0.4030, 6: 0.3742,
            7: 0.3534, 8: 0.3375, 9: 0.3249, 10: 0.3146}
# D4 for range chart, indexed by subgroup size (= trials)
D4_TABLE = {2: 3.267, 3: 2.574, 4: 2.282, 5: 2.114, 6: 2.004, 7: 1.924,
            8: 1.864, 9: 1.816, 10: 1.777}
D3_TABLE = {2: 0.0, 3: 0.0, 4: 0.0, 5: 0.0, 6: 0.0, 7: 0.076, 8: 0.136,
            9: 0.184, 10: 0.223}

NDC_FACTOR = 1.41  # AIAG: ndc = 1.41 * PV / GRR, truncated


# ---------------------------------------------------------------------------
# Range-distribution constants (d2, d3, d2*) computed numerically
# ---------------------------------------------------------------------------

_RANGE_CACHE: dict[int, tuple[float, float]] = {}


def _norm_cdf(x: np.ndarray) -> np.ndarray:
    return 0.5 * (1.0 + np.vectorize(math.erf)(x / math.sqrt(2.0)))


def _range_moments(m: int) -> tuple[float, float]:
    """Return (E[W], E[W^2]) of the range W of m iid N(0,1) variables."""
    if m < 2:
        raise ValueError("range needs a subgroup size of at least 2")
    if m in _RANGE_CACHE:
        return _RANGE_CACHE[m]
    h = 0.01
    x = np.arange(-8.0, 8.0 + h / 2, h)
    cdf = _norm_cdf(x)
    pdf = np.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)
    # E[W] = integral of 1 - Phi^m - (1-Phi)^m
    ew = float((1.0 - cdf ** m - (1.0 - cdf) ** m).sum() * h)  # integrand ~0 at both ends
    # E[W^2] from the joint density of (min=x, max=y)
    diff = cdf[None, :] - cdf[:, None]           # Phi(y) - Phi(x), rows x, cols y
    mask = diff > 0
    dens = np.zeros_like(diff)
    dens[mask] = m * (m - 1) * diff[mask] ** (m - 2)
    dens *= pdf[:, None] * pdf[None, :]
    w2 = (x[None, :] - x[:, None]) ** 2
    ew2 = float((w2 * dens).sum() * h * h)
    _RANGE_CACHE[m] = (ew, ew2)
    return ew, ew2


def d2(m: int) -> float:
    """Mean of the relative range for subgroup size m."""
    return _range_moments(int(m))[0]


def d3(m: int) -> float:
    ew, ew2 = _range_moments(int(m))
    return math.sqrt(max(ew2 - ew * ew, 0.0))


def d2_star(m: int, g: int = 1) -> float:
    """AIAG d2* for subgroup size m and g subgroups: sqrt(d2^2 + d3^2/g) (exact for g=1)."""
    ew, ew2 = _range_moments(int(m))
    var = ew2 - ew * ew
    return math.sqrt(ew * ew + var / g)


def _k1(trials: int) -> float:
    return K1_TABLE.get(trials) or 1.0 / d2(trials)


def _k2(appraisers: int) -> float:
    if appraisers == 1:
        return 0.0
    return K2_TABLE.get(appraisers) or 1.0 / d2_star(appraisers, 1)


def _k3(parts: int) -> float:
    return K3_TABLE.get(parts) or 1.0 / d2_star(parts, 1)


def _d4(m: int) -> float:
    if m in D4_TABLE:
        return D4_TABLE[m]
    return 1.0 + 3.0 * d3(m) / d2(m)


def _d3c(m: int) -> float:
    if m in D3_TABLE:
        return D3_TABLE[m]
    return max(0.0, 1.0 - 3.0 * d3(m) / d2(m))


# ---------------------------------------------------------------------------
# Distribution functions (scipy when present, built-in otherwise)
# ---------------------------------------------------------------------------

def _scipy_stats():
    try:
        from scipy import stats  # type: ignore
        return stats
    except Exception:  # pragma: no cover - depends on deployment
        return None


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the incomplete beta (modified Lentz)."""
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = tiny if abs(d) < tiny else d
    d = 1.0 / d
    h = d
    for m in range(1, 1000):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = tiny if abs(d) < tiny else d
        c = 1.0 + aa / c
        c = tiny if abs(c) < tiny else c
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = tiny if abs(d) < tiny else d
        c = 1.0 + aa / c
        c = tiny if abs(c) < tiny else c
        d = 1.0 / d
        de = d * c
        h *= de
        if abs(de - 1.0) < 1e-15:
            break
    return h


def betainc(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbt = (math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
           + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return math.exp(lbt) * _betacf(a, b, x) / a
    return 1.0 - math.exp(lbt) * _betacf(b, a, 1.0 - x) / b


def _t_sf_builtin(t: float, df: float) -> float:
    """One-sided upper tail P(T > t)."""
    if math.isinf(t):
        return 0.0 if t > 0 else 1.0
    x = df / (df + t * t)
    tail = 0.5 * betainc(df / 2.0, 0.5, x)
    return tail if t >= 0 else 1.0 - tail


def t_sf(t: float, df: float) -> float:
    st = _scipy_stats()
    if st is not None:
        return float(st.t.sf(t, df))
    return _t_sf_builtin(t, df)


def t_ppf(q: float, df: float) -> float:
    """Quantile of Student's t (q in (0,1))."""
    st = _scipy_stats()
    if st is not None:
        return float(st.t.ppf(q, df))
    if q == 0.5:
        return 0.0
    if q < 0.5:
        return -t_ppf(1.0 - q, df)
    target = 1.0 - q  # upper tail
    lo, hi = 0.0, 1.0
    while _t_sf_builtin(hi, df) > target:
        hi *= 2.0
        if hi > 1e12:
            return math.inf
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if _t_sf_builtin(mid, df) > target:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-12 * max(1.0, hi):
            break
    return 0.5 * (lo + hi)


def f_sf(f: float, df1: float, df2: float) -> float:
    """Upper tail P(F > f)."""
    if f is None or not math.isfinite(f):
        return 0.0 if f is not None and f > 0 else float("nan")
    if f <= 0:
        return 1.0
    st = _scipy_stats()
    if st is not None:
        return float(st.f.sf(f, df1, df2))
    return betainc(df2 / 2.0, df1 / 2.0, df2 / (df2 + df1 * f))


def _beta_ppf(q: float, a: float, b: float) -> float:
    st = _scipy_stats()
    if st is not None:
        return float(st.beta.ppf(q, a, b))
    lo, hi = 0.0, 1.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if betainc(a, b, mid) < q:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def _clopper_pearson(x: int, n: int, alpha: float = 0.05) -> tuple[float, float]:
    if n == 0:
        return (float("nan"), float("nan"))
    lo = 0.0 if x == 0 else _beta_ppf(alpha / 2.0, x, n - x + 1)
    hi = 1.0 if x == n else _beta_ppf(1.0 - alpha / 2.0, x + 1, n - x)
    return (100.0 * lo, 100.0 * hi)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _names_and_values(data: Any) -> tuple[list[str], list]:
    """Accept either a list (appraisers A, B, C...) or a dict {name: trials}."""
    if isinstance(data, dict):
        return [str(k) for k in data.keys()], list(data.values())
    names = []
    for i in range(len(data)):
        names.append(chr(ord("A") + i) if i < 26 else f"P{i + 1}")
    return names, list(data)


def _to_cube(data: Any) -> tuple[list[str], np.ndarray]:
    names, values = _names_and_values(data)
    if not values:
        raise ValueError("no appraisers")
    try:
        arr = np.array(values, dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError("data must be a balanced numeric [appraiser][trial][part] array") from exc
    if arr.ndim != 3:
        raise ValueError("data must be a balanced [appraiser][trial][part] array (equal sizes)")
    if not np.all(np.isfinite(arr)):
        raise ValueError("data contains missing or non-finite values; a balanced study is required")
    k, r, n = arr.shape
    if r < 2:
        raise ValueError("at least 2 trials are required")
    if n < 2:
        raise ValueError("at least 2 parts are required")
    return names, arr


def _verdict_pct(pct: float | None) -> str | None:
    if pct is None or not math.isfinite(pct):
        return None
    if pct < 10.0:
        return "acceptable"
    if pct <= 30.0:
        return "conditional"
    return "unacceptable"


def _ndc(pv: float, grr: float) -> int | None:
    if grr <= 0:
        return None
    return int(math.floor(NDC_FACTOR * pv / grr + 1e-12))


def _pct(x: float, base: float) -> float | None:
    if base is None or base == 0 or not math.isfinite(base):
        return None
    return 100.0 * x / base


def _summary(sd: dict[str, float], tolerance: float | None, k_sigma: float,
             basis: str) -> dict[str, Any]:
    """Common percentage / ndc / verdict block. sd keys: ev, av, (int), grr, pv, tv."""
    out: dict[str, Any] = {}
    tv = sd["tv"]
    for key, val in sd.items():
        if key == "tv":
            continue
        out[f"pct_{key}"] = _pct(val, tv)
    if tolerance is not None:
        if tolerance <= 0:
            raise ValueError("tolerance must be positive")
        for key, val in sd.items():
            out[f"pct_tol_{key}"] = 100.0 * k_sigma * val / tolerance
    out["ndc"] = _ndc(sd["pv"], sd["grr"])
    basis = (basis or "tv").lower()
    if basis not in ("tv", "tolerance"):
        raise ValueError("basis must be 'tv' or 'tolerance'")
    if basis == "tolerance" and tolerance is None:
        raise ValueError("basis='tolerance' needs a tolerance")
    pct_basis = out["pct_tol_grr"] if basis == "tolerance" else out["pct_grr"]
    out["basis"] = basis
    out["pct_grr_basis"] = pct_basis
    out["verdict"] = _verdict_pct(pct_basis)
    notes = []
    if out["ndc"] is not None and out["ndc"] < 5:
        notes.append("ndc < 5: measurement system cannot adequately discriminate between parts")
    out["notes"] = notes
    return out


# ---------------------------------------------------------------------------
# 1. Average & Range method
# ---------------------------------------------------------------------------

def grr_xbar_r(data: Any, tolerance: float | None = None, k_sigma: float = 6,
               basis: str = "tv") -> dict[str, Any]:
    """Gauge R&R by the Average & Range method (AIAG MSA 4th ed., Fig. III-B 15/16).

    data[appraiser][trial][part] (list or dict {appraiser_name: trials}).
    tolerance: total tolerance (USL-LSL) for %tolerance; k_sigma: study-variation multiplier.
    basis: 'tv' (verdict on %GRR of total variation) or 'tolerance'.
    """
    names, arr = _to_cube(data)
    k, r, n = arr.shape

    ranges = arr.max(axis=1) - arr.min(axis=1)          # [appraiser][part]
    r_app = ranges.mean(axis=1)                          # R̄ per appraiser
    rbar = float(r_app.mean())
    x_app = arr.mean(axis=(1, 2))                        # X̄ per appraiser
    xdiff = float(x_app.max() - x_app.min())
    part_means = arr.mean(axis=(0, 1))
    rp = float(part_means.max() - part_means.min())
    xbarbar = float(arr.mean())

    d4c, d3c = _d4(r), _d3c(r)
    ucl_r, lcl_r = d4c * rbar, d3c * rbar
    out_of_control = [
        {"appraiser": names[i], "part": j + 1, "range": float(ranges[i, j])}
        for i in range(k) for j in range(n)
        if ranges[i, j] > ucl_r + 1e-12 or ranges[i, j] < lcl_r - 1e-12
    ]

    k1, k2, k3 = _k1(r), _k2(k), _k3(n)
    ev = rbar * k1
    av_sq = (xdiff * k2) ** 2 - ev ** 2 / (n * r)
    av = math.sqrt(av_sq) if av_sq > 0 else 0.0
    grr = math.hypot(ev, av)
    pv = rp * k3
    tv = math.hypot(grr, pv)

    sd = {"ev": ev, "av": av, "grr": grr, "pv": pv, "tv": tv}
    res: dict[str, Any] = {
        "method": "xbar_r",
        "appraisers": names,
        "n_appraisers": k, "n_trials": r, "n_parts": n,
        "appraiser_means": [float(v) for v in x_app],
        "appraiser_rbar": [float(v) for v in r_app],
        "part_means": [float(v) for v in part_means],
        "xbarbar": xbarbar,
        "rbar": rbar, "xdiff": xdiff, "rp": rp,
        "d4": d4c, "ucl_r": ucl_r, "lcl_r": lcl_r,
        "ranges_out_of_control": out_of_control,
        "k1": k1, "k2": k2, "k3": k3,
        **sd,
        "k_sigma": k_sigma, "tolerance": tolerance,
    }
    res.update(_summary(sd, tolerance, k_sigma, basis))
    if av_sq <= 0 and k > 1:
        res["notes"].append("AV term under the root was negative; AV set to 0")
    if out_of_control:
        res["notes"].append(
            f"{len(out_of_control)} range(s) outside the R-chart limits: "
            "investigate/remeasure before relying on the study")
    return res


# ---------------------------------------------------------------------------
# 2. ANOVA method
# ---------------------------------------------------------------------------

def _row(ss: float, df: int, denom_ms: float | None, denom_df: int | None) -> dict[str, Any]:
    ms = ss / df if df > 0 else float("nan")
    row: dict[str, Any] = {"ss": ss, "df": df, "ms": ms}
    if denom_ms is not None:
        f = ms / denom_ms if denom_ms > 0 else (math.inf if ms > 0 else float("nan"))
        row["f"] = f
        if math.isnan(f):
            row["p"] = None
        else:
            row["p"] = 0.0 if math.isinf(f) else f_sf(f, df, denom_df)
    return row


def grr_anova(data: Any, tolerance: float | None = None, alpha_interaction: float = 0.25,
              k_sigma: float = 6, basis: str = "tv") -> dict[str, Any]:
    """Gauge R&R by two-way crossed ANOVA with interaction (AIAG MSA 4th ed. Tables III-B 7/8).

    ``table`` is the full model as printed in the AIAG manual: appraiser and part are tested
    against the appraiser*part interaction MS, the interaction against equipment (repeatability).
    If the interaction p-value exceeds ``alpha_interaction`` (Minitab default 0.25) it is pooled
    into the error term (``pooled: True``); the variance components then use the pooled MS and
    ``table_reduced`` gives the reduced (no-interaction) ANOVA table.
    """
    names, arr = _to_cube(data)
    k, r, n = arr.shape
    N = k * r * n
    gm = float(arr.mean())
    a_mean = arr.mean(axis=(1, 2))
    p_mean = arr.mean(axis=(0, 1))
    cell = arr.mean(axis=1)                               # [appraiser][part]

    ss_a = float(n * r * ((a_mean - gm) ** 2).sum())
    ss_p = float(k * r * ((p_mean - gm) ** 2).sum())
    ss_cells = float(r * ((cell - gm) ** 2).sum())
    ss_ap = max(ss_cells - ss_a - ss_p, 0.0)
    ss_e = float(((arr - cell[:, None, :]) ** 2).sum())
    ss_t = float(((arr - gm) ** 2).sum())

    df_a, df_p, df_ap, df_e, df_t = k - 1, n - 1, (k - 1) * (n - 1), k * n * (r - 1), N - 1
    ms_e = ss_e / df_e
    ms_ap = ss_ap / df_ap if df_ap > 0 else float("nan")

    if k > 1:
        inter = _row(ss_ap, df_ap, ms_e, df_e)
        table = {
            "appraiser": _row(ss_a, df_a, ms_ap, df_ap),
            "part": _row(ss_p, df_p, ms_ap, df_ap),
            "interaction": inter,
            "repeatability": {"ss": ss_e, "df": df_e, "ms": ms_e},
            "total": {"ss": ss_t, "df": df_t},
        }
        p_int = inter.get("p")
        pooled = bool(p_int is not None and math.isfinite(p_int) and p_int > alpha_interaction)
    else:  # single appraiser: one-way part ANOVA
        table = {
            "part": _row(ss_p, df_p, ms_e, df_e),
            "repeatability": {"ss": ss_e, "df": df_e, "ms": ms_e},
            "total": {"ss": ss_t, "df": df_t},
        }
        p_int = None
        pooled = False

    res: dict[str, Any] = {
        "method": "anova",
        "appraisers": names,
        "n_appraisers": k, "n_trials": r, "n_parts": n,
        "grand_mean": gm,
        "table": table,
        "alpha_interaction": alpha_interaction,
        "interaction_p": p_int,
        "pooled": pooled,
    }

    if pooled:
        df_pool = df_ap + df_e
        ms_pool = (ss_ap + ss_e) / df_pool
        res["table_reduced"] = {
            "appraiser": _row(ss_a, df_a, ms_pool, df_pool),
            "part": _row(ss_p, df_p, ms_pool, df_pool),
            "repeatability": {"ss": ss_ap + ss_e, "df": df_pool, "ms": ms_pool},
            "total": {"ss": ss_t, "df": df_t},
        }
        var_e = ms_pool
        var_int = 0.0
        var_a = max((table["appraiser"]["ms"] - ms_pool) / (n * r), 0.0)
        var_p = max((table["part"]["ms"] - ms_pool) / (k * r), 0.0)
        res["pooling_note"] = (f"interaction p = {p_int:.4g} > {alpha_interaction}: "
                               "interaction pooled into repeatability (equipment) error")
    elif k > 1:
        var_e = ms_e
        var_int = max((ms_ap - ms_e) / r, 0.0)
        var_a = max((table["appraiser"]["ms"] - ms_ap) / (n * r), 0.0)
        var_p = max((table["part"]["ms"] - ms_ap) / (k * r), 0.0)
    else:
        var_e = ms_e
        var_int = var_a = 0.0
        var_p = max((table["part"]["ms"] - ms_e) / r, 0.0)

    var_repro = var_a + var_int
    var_grr = var_e + var_repro
    var_tv = var_grr + var_p
    variance = {"repeatability": var_e, "appraiser": var_a, "interaction": var_int,
                "reproducibility": var_repro, "grr": var_grr, "part": var_p, "total": var_tv}
    res["variance"] = variance
    res["pct_contribution"] = {key: _pct(v, var_tv) for key, v in variance.items()}

    sd = {"ev": math.sqrt(var_e), "av": math.sqrt(var_a), "int": math.sqrt(var_int),
          "reproducibility": math.sqrt(var_repro), "grr": math.sqrt(var_grr),
          "pv": math.sqrt(var_p), "tv": math.sqrt(var_tv)}
    res.update(sd)
    res["study_var"] = {key: k_sigma * v for key, v in sd.items()}
    res["k_sigma"] = k_sigma
    res["tolerance"] = tolerance
    res.update(_summary(sd, tolerance, k_sigma, basis))
    if pooled:
        res["notes"].append(res["pooling_note"])
    return res


# ---------------------------------------------------------------------------
# 3. Bias
# ---------------------------------------------------------------------------

def bias(readings: Sequence[float], reference: float, tolerance: float | None = None,
         alpha: float = 0.05) -> dict[str, Any]:
    """Independent-sample bias study (AIAG MSA 4th ed. ch. III-B, p. 87-90)."""
    x = np.asarray(list(readings), dtype=float)
    if x.ndim != 1 or x.size < 2:
        raise ValueError("bias needs at least 2 readings")
    if not np.all(np.isfinite(x)):
        raise ValueError("readings contain missing or non-finite values")
    n = int(x.size)
    mean = float(x.mean())
    b = mean - float(reference)
    rng = float(x.max() - x.min())
    sr = float(x.std(ddof=1))
    sb = sr / math.sqrt(n)
    df = n - 1
    if sb > 0:
        t = b / sb
        p = 2.0 * t_sf(abs(t), df)
    else:
        t = 0.0 if b == 0 else math.copysign(math.inf, b)
        p = 1.0 if b == 0 else 0.0
    tcrit = t_ppf(1.0 - alpha / 2.0, df)
    lo, hi = b - tcrit * sb, b + tcrit * sb
    res: dict[str, Any] = {
        "n": n, "reference": float(reference), "mean": mean, "bias": b,
        "range": rng, "sigma_r_range": rng / d2_star(n, 1) if n <= 25 else None,
        "sr": sr, "sb": sb, "t": t, "df": df, "p": p, "alpha": alpha,
        "t_crit": tcrit, "ci_low": lo, "ci_high": hi,
        "acceptable": bool(lo <= 0.0 <= hi),
        "tolerance": tolerance,
        "pct_bias_tol": (100.0 * abs(b) / tolerance) if tolerance else None,
    }
    res["verdict"] = "acceptable" if res["acceptable"] else "unacceptable"
    return res


# ---------------------------------------------------------------------------
# 4. Linearity
# ---------------------------------------------------------------------------

def linearity(parts: Iterable[dict], alpha: float = 0.05, process_variation: float | None = None,
              band_points: int | None = None) -> dict[str, Any]:
    """Linearity study (AIAG MSA 4th ed. p. 96-100).

    parts = [{'reference': x, 'readings': [...]}, ...].  Regresses every individual bias
    (reading - reference) on the reference value: bias = b + a * reference.
    Linearity is acceptable when neither slope nor intercept is significant at ``alpha``
    (equivalently the bias = 0 line lies within the confidence bands).
    """
    xs: list[float] = []
    ys: list[float] = []
    per_part = []
    for p in parts:
        ref = float(p["reference"])
        rd = np.asarray(list(p["readings"]), dtype=float)
        if rd.size == 0 or not np.all(np.isfinite(rd)):
            raise ValueError("each part needs finite readings")
        bb = rd - ref
        xs.extend([ref] * rd.size)
        ys.extend(bb.tolist())
        per_part.append({"reference": ref, "n": int(rd.size), "mean": float(rd.mean()),
                         "bias": float(bb.mean())})
    x = np.asarray(xs)
    y = np.asarray(ys)
    gm = x.size
    if len({pp["reference"] for pp in per_part}) < 2 or gm < 3:
        raise ValueError("linearity needs at least 2 distinct reference values and 3 readings")
    xm, ym = float(x.mean()), float(y.mean())
    sxx = float(((x - xm) ** 2).sum())
    sxy = float(((x - xm) * (y - ym)).sum())
    a = sxy / sxx
    b = ym - a * xm
    resid = y - (b + a * x)
    sse = float((resid ** 2).sum())
    sst = float(((y - ym) ** 2).sum())
    df = gm - 2
    s = math.sqrt(sse / df)
    r2 = 1.0 - sse / sst if sst > 0 else 1.0
    se_a = s / math.sqrt(sxx)
    se_b = s * math.sqrt(1.0 / gm + xm * xm / sxx)
    ta = a / se_a if se_a > 0 else math.copysign(math.inf, a) if a else 0.0
    tb = b / se_b if se_b > 0 else math.copysign(math.inf, b) if b else 0.0
    tcrit = t_ppf(1.0 - alpha / 2.0, df)
    pa = 2.0 * t_sf(abs(ta), df)
    pb = 2.0 * t_sf(abs(tb), df)
    slope_sig = abs(ta) > tcrit
    int_sig = abs(tb) > tcrit

    refs = sorted({pp["reference"] for pp in per_part})
    if band_points and band_points >= 2:
        grid = np.linspace(refs[0], refs[-1], band_points).tolist()
    else:
        grid = refs
    bands = []
    for x0 in grid:
        fit = b + a * x0
        half = tcrit * s * math.sqrt(1.0 / gm + (x0 - xm) ** 2 / sxx)
        bands.append({"x": float(x0), "fit": fit, "lower": fit - half, "upper": fit + half})
    zero_inside = all(bd["lower"] <= 0.0 <= bd["upper"] for bd in bands)

    res: dict[str, Any] = {
        "n": gm, "df": df, "alpha": alpha,
        "slope": a, "intercept": b, "r2": r2, "s": s,
        "se_slope": se_a, "se_intercept": se_b,
        "t_slope": ta, "t_intercept": tb, "p_slope": pa, "p_intercept": pb,
        "t_crit": tcrit,
        "slope_significant": bool(slope_sig), "intercept_significant": bool(int_sig),
        "zero_line_within_bands": bool(zero_inside),
        "acceptable": bool(not slope_sig and not int_sig),
        "per_part": per_part,
        "bands": bands,
        "pct_linearity": 100.0 * abs(a),
        "linearity": abs(a) * process_variation if process_variation else None,
    }
    res["verdict"] = "acceptable" if res["acceptable"] else "unacceptable"
    return res


# ---------------------------------------------------------------------------
# 5. Attribute agreement
# ---------------------------------------------------------------------------

_GOOD_STRINGS = {"OK", "PASS", "GOOD", "G", "ACCEPT", "ACC", "Y", "YES", "TRUE", "1", "合格"}


def _is_good(v: Any) -> bool:
    if isinstance(v, str):
        return v.strip().upper() in _GOOD_STRINGS
    return bool(v)


def _cohen_kappa(pairs: list[tuple[Any, Any]]) -> dict[str, Any]:
    n = len(pairs)
    cats = sorted({c for pr in pairs for c in pr}, key=lambda v: (str(type(v)), str(v)))
    agree = sum(1 for u, v in pairs if u == v)
    po = agree / n
    pe = sum((sum(1 for u, _ in pairs if u == c) / n) * (sum(1 for _, v in pairs if v == c) / n)
             for c in cats)
    kappa = (po - pe) / (1.0 - pe) if pe < 1.0 else None
    table = {str(c1): {str(c2): sum(1 for u, v in pairs if u == c1 and v == c2) for c2 in cats}
             for c1 in cats}
    return {"kappa": kappa, "po": po, "pe": pe, "n": n, "crosstab": table}


def _fleiss_kappa(ratings: list[list[Any]]) -> float | None:
    """ratings[subject] = list of category labels (same number of raters per subject)."""
    cats = sorted({c for row in ratings for c in row}, key=lambda v: (str(type(v)), str(v)))
    N = len(ratings)
    m = len(ratings[0])
    if m < 2:
        return None
    counts = np.array([[row.count(c) for c in cats] for row in ratings], dtype=float)
    p_i = ((counts ** 2).sum(axis=1) - m) / (m * (m - 1))
    p_bar = float(p_i.mean())
    p_j = counts.sum(axis=0) / (N * m)
    pe = float((p_j ** 2).sum())
    if pe >= 1.0:
        return None
    return (p_bar - pe) / (1.0 - pe)


def _kappa_verdict(kp: float | None) -> str | None:
    if kp is None:
        return None
    if kp > 0.75:
        return "good"
    if kp >= 0.40:
        return "marginal"
    return "poor"


def _rate_verdict(eff: float | None, miss: float | None, fa: float | None) -> str | None:
    if eff is None:
        return None
    if eff < 80.0 or (miss is not None and miss > 5.0) or (fa is not None and fa > 10.0):
        return "unacceptable"
    if eff >= 90.0 and (miss is None or miss <= 2.0) and (fa is None or fa <= 5.0):
        return "acceptable"
    return "marginal"


def _agree_block(x: int, n: int) -> dict[str, Any]:
    lo, hi = _clopper_pearson(x, n)
    return {"matched": x, "inspected": n, "pct": 100.0 * x / n if n else None,
            "ci95_low": lo, "ci95_high": hi}


def attribute_agreement(results: Any, reference: Sequence[Any] | None = None,
                        good_values: Iterable[Any] | None = None) -> dict[str, Any]:
    """Attribute agreement analysis (AIAG MSA 4th ed. ch. III-C, cross-tab/kappa method).

    results[appraiser][trial][part] with categorical values (or dict {name: trials}).
    reference[part] = known standard value (optional).
    good_values: values that mean "acceptable" for effectiveness/miss/false-alarm; default:
    truthy numbers / 'OK' / 'PASS' / 'GOOD' / 'Y' / ... (anything else is "bad").
    """
    names, vals = _names_and_values(results)
    k = len(vals)
    if k == 0:
        raise ValueError("no appraisers")
    r = len(vals[0])
    if r == 0:
        raise ValueError("no trials")
    n = len(vals[0][0])
    for a in vals:
        if len(a) != r or any(len(t) != n for t in a):
            raise ValueError("results must be balanced [appraiser][trial][part]")
    if any(v is None for a in vals for t in a for v in t):
        raise ValueError("results contain missing values")
    if reference is not None:
        reference = list(reference)
        if len(reference) != n:
            raise ValueError("reference must have one value per part")

    if good_values is not None:
        gset = set(good_values)
        is_good = lambda v: v in gset  # noqa: E731
    else:
        is_good = _is_good

    res: dict[str, Any] = {"appraisers": names, "n_appraisers": k, "n_trials": r, "n_parts": n}

    # within appraiser / vs standard
    within, vs_std, within_kappa = {}, {}, {}
    for i, nm in enumerate(names):
        same = [all(vals[i][t][p] == vals[i][0][p] for t in range(r)) for p in range(n)]
        within[nm] = _agree_block(sum(same), n) if r > 1 else None
        within_kappa[nm] = _fleiss_kappa([[vals[i][t][p] for t in range(r)] for p in range(n)]) \
            if r > 1 else None
        if reference is not None:
            ok = [same[p] and vals[i][0][p] == reference[p] for p in range(n)]
            vs_std[nm] = _agree_block(sum(ok), n)
    res["within_appraiser"] = within
    res["within_kappa"] = within_kappa

    every = [[vals[i][t][p] for i in range(k) for t in range(r)] for p in range(n)]
    all_same = [all(v == row[0] for v in row) for row in every]
    res["between_appraisers"] = _agree_block(sum(all_same), n) if k > 1 else None
    res["fleiss_kappa"] = _fleiss_kappa(every) if k * r > 1 else None

    # pairwise Cohen kappa (pooled over trials: appraiser i trial t vs appraiser j trial t)
    pair_kappa = []
    for i in range(k):
        for j in range(i + 1, k):
            pairs = [(vals[i][t][p], vals[j][t][p]) for t in range(r) for p in range(n)]
            ck = _cohen_kappa(pairs)
            pair_kappa.append({"a": names[i], "b": names[j], **ck,
                               "rating": _kappa_verdict(ck["kappa"])})
    res["kappa_pairs"] = pair_kappa

    if reference is not None:
        res["each_vs_standard"] = vs_std
        all_ok = [all_same[p] and every[p][0] == reference[p] for p in range(n)]
        res["all_vs_standard"] = _agree_block(sum(all_ok), n)
        kref = {}
        for i, nm in enumerate(names):
            pairs = [(vals[i][t][p], reference[p]) for t in range(r) for p in range(n)]
            ck = _cohen_kappa(pairs)
            kref[nm] = {**ck, "rating": _kappa_verdict(ck["kappa"])}
        res["kappa_vs_reference"] = kref

        cats = set(reference) | {v for a in vals for t in a for v in t}
        binary = len(cats) <= 2
        res["binary"] = binary
        if binary:
            ref_good = [is_good(v) for v in reference]
            n_good = sum(ref_good)
            n_bad = n - n_good
            rates = {}
            for i, nm in enumerate(names):
                miss = sum(1 for t in range(r) for p in range(n)
                           if not ref_good[p] and is_good(vals[i][t][p]))
                fa = sum(1 for t in range(r) for p in range(n)
                         if ref_good[p] and not is_good(vals[i][t][p]))
                eff = vs_std[nm]["pct"]
                miss_rate = 100.0 * miss / (n_bad * r) if n_bad else None
                fa_rate = 100.0 * fa / (n_good * r) if n_good else None
                rates[nm] = {
                    "effectiveness": eff,
                    "misses": miss, "miss_opportunities": n_bad * r, "miss_rate": miss_rate,
                    "false_alarms": fa, "false_alarm_opportunities": n_good * r,
                    "false_alarm_rate": fa_rate,
                    "verdict": _rate_verdict(eff, miss_rate, fa_rate),
                }
            res["rates"] = rates

    # overall verdict: worst of the kappa ratings and per-appraiser rate verdicts
    order = {"good": 0, "acceptable": 0, "marginal": 1, "poor": 2, "unacceptable": 2}
    ratings = [pk["rating"] for pk in pair_kappa]
    if reference is not None:
        ratings += [kv["rating"] for kv in res["kappa_vs_reference"].values()]
        ratings += [rv["verdict"] for rv in res.get("rates", {}).values()]
    ratings = [x for x in ratings if x is not None]
    if ratings:
        worst = max(order[x] for x in ratings)
        res["verdict"] = ["acceptable", "marginal", "unacceptable"][worst]
    else:
        res["verdict"] = None
    return res


# ---------------------------------------------------------------------------
# 6. Display helper
# ---------------------------------------------------------------------------

def report_rows(result: Any, prefix: str = "") -> list[tuple[str, Any]]:
    """Flatten a result dict into [(label, value)] rows (dotted labels, lists indexed)."""
    rows: list[tuple[str, Any]] = []
    if isinstance(result, dict):
        for key, val in result.items():
            label = f"{prefix}.{key}" if prefix else str(key)
            rows.extend(report_rows(val, label))
    elif isinstance(result, (list, tuple)):
        if all(not isinstance(v, (dict, list, tuple)) for v in result):
            rows.append((prefix, ", ".join(_fmt(v) for v in result)))
        else:
            for i, val in enumerate(result):
                rows.extend(report_rows(val, f"{prefix}[{i}]"))
    else:
        rows.append((prefix, result))
    return rows


def _fmt(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:.6g}"
    return str(v)
