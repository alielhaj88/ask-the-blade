"""
twist_opt.py -- Phase 9: find the twist distribution that gives the best Cp.

Same trick as chord_opt.py: each ring is solved on its own, so the power of
ring i depends only on that ring's chord and twist. Keep the baseline chord,
shift the twist of every ring by the same offset, and keep the best offset
per ring.
"""
import io
import os
from contextlib import redirect_stdout

import numpy as np
from bem_core import design_blade, solve_rotor, V_inf, R, r

OFFSET_MIN = -15.0   # degrees, largest twist decrease we try
OFFSET_MAX = 8.0     # degrees, largest twist increase we try
OFFSET_STEP = 0.25   # degrees


def optimize_twist(lambda_design, alpha_design_deg, num_blades, chord_cap_m, airfoil="standin", a_target=1/3):
    """Return baseline and best-found twist for one design. Chord stays at the baseline chord."""
    omega = lambda_design * V_inf / R

    c, beta_base = design_blade(omega, alpha_design_deg, a_target, chord_cap_m, num_blades, airfoil)
    base = solve_rotor(omega, c, beta_base, num_blades, airfoil)

    best_power = np.where(base["converged"], base["dQ"] * omega, -np.inf)
    best_beta = beta_base.copy()

    for offset_deg in np.arange(OFFSET_MIN, OFFSET_MAX + 1e-9, OFFSET_STEP):
        beta_try = beta_base + np.radians(offset_deg)
        with redirect_stdout(io.StringIO()), np.errstate(all="ignore"):
            res = solve_rotor(omega, c, beta_try, num_blades, airfoil)
        ring_power = res["dQ"] * omega
        better = (ring_power > best_power) & res["converged"]
        best_power[better] = ring_power[better]
        best_beta[better] = beta_try[better]

    best = solve_rotor(omega, c, best_beta, num_blades, airfoil)
    offsets_deg = np.degrees(best_beta - beta_base)
    at_limit = (offsets_deg <= OFFSET_MIN + 1e-6) | (offsets_deg >= OFFSET_MAX - 1e-6)

    return {
        "cp_baseline": round(float(base["Cp"]), 4),
        "cp_best_found": round(float(best["Cp"]), 4),
        "cp_change": round(float(best["Cp"] - base["Cp"]), 4),
        "thrust_baseline_N": round(float(base["T"])),
        "thrust_best_found_N": round(float(best["T"])),
        "twist_baseline_deg": np.degrees(beta_base).round(2).tolist(),
        "twist_best_found_deg": np.degrees(best_beta).round(2).tolist(),
        "twist_change_deg": offsets_deg.round(2).tolist(),
        "chord_m": c.round(3).tolist(),
        "radius_m": r.round(2).tolist(),
        "airfoil": airfoil,
        "rings_at_search_limit": int(np.sum(at_limit)),
        "aoa_best_found_deg": np.degrees(best["alpha"]).round(2).tolist(),
        "baseline_all_rings_converged": bool(base["converged"].all()),
        "all_rings_converged": bool(best["converged"].all()),
    }


def plot_twist(result, path="twist_profile.png"):
    """Save baseline vs best-found twist along the span."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(9, 3.6))
    ax.plot(result["radius_m"], result["twist_baseline_deg"], "o-", label="baseline")
    ax.plot(result["radius_m"], result["twist_best_found_deg"], "s-", label="best found")
    ax.set_xlabel("radius (m)")
    ax.set_ylabel("twist (deg)")
    ax.set_title(f"Twist: Cp {result['cp_baseline']} -> {result['cp_best_found']}")
    ax.grid(alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return os.path.abspath(path)


if __name__ == "__main__":
    out = optimize_twist(lambda_design=7, alpha_design_deg=6, num_blades=3, chord_cap_m=2.0)
    for k, v in out.items():
        print(k, v)
    print("saved", plot_twist(out))
