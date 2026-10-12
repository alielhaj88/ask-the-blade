"""
chord_opt.py -- Phase 8: find the chord distribution that gives the best Cp.

Idea: in BEM every ring is solved on its own, so the power of ring i depends
only on the chord of ring i. That means we can pick the best chord for each
ring separately. No optimiser library needed: try many chords, keep the best
one per ring.
"""
import io
import os
from contextlib import redirect_stdout

import numpy as np
from bem_core import design_blade, solve_rotor, V_inf, R, r, N

C_MIN = 0.1          # m, thinnest chord we try
C_STEP = 0.05        # m, spacing between tried chords


def optimize_chord(lambda_design, alpha_design_deg, num_blades, chord_cap_m, airfoil="standin", a_target=1/3):
    """Return baseline and best-found chord for one design. Twist stays at the baseline twist."""
    omega = lambda_design * V_inf / R

    # Baseline blade from design_blade
    c_base, beta = design_blade(omega, alpha_design_deg, a_target, chord_cap_m, num_blades, airfoil)
    base = solve_rotor(omega, c_base, beta, num_blades, airfoil)

    # Start from the baseline: per-ring power = ring torque * omega
    best_power = np.where(base["converged"], base["dQ"] * omega, -np.inf)
    best_c = c_base.copy()

    # Try one chord at a time on ALL rings, then keep the winner per ring
    for c in np.arange(C_MIN, chord_cap_m + 1e-9, C_STEP):
        with redirect_stdout(io.StringIO()), np.errstate(all="ignore"):   # hide warnings from bad chords
            res = solve_rotor(omega, np.full(N, c), beta, num_blades, airfoil)
        ring_power = res["dQ"] * omega
        better = (ring_power > best_power) & res["converged"]
        best_power[better] = ring_power[better]
        best_c[better] = c

    best = solve_rotor(omega, best_c, beta, num_blades, airfoil)

    return {
        "cp_baseline": round(float(base["Cp"]), 4),
        "cp_best_found": round(float(best["Cp"]), 4),
        "cp_change": round(float(best["Cp"] - base["Cp"]), 4),
        "thrust_baseline_N": round(float(base["T"])),
        "thrust_best_found_N": round(float(best["T"])),
        "chord_baseline_m": c_base.round(3).tolist(),
        "chord_best_found_m": best_c.round(3).tolist(),
        "twist_deg": np.degrees(beta).round(2).tolist(),
        "radius_m": r.round(2).tolist(),
        "airfoil": airfoil,
        "rings_at_chord_cap": int(np.sum(best_c >= chord_cap_m - 1e-9)),
        "baseline_all_rings_converged": bool(base["converged"].all()),
        "all_rings_converged": bool(best["converged"].all()),
    }


def plot_planform(result, path="chord_planform.png"):
    """Save a top-down picture of the blade: baseline outline vs best-found outline."""
    import matplotlib
    matplotlib.use("Agg")                  # save to file, never open a window
    import matplotlib.pyplot as plt

    rr = np.array(result["radius_m"])
    fig, ax = plt.subplots(figsize=(10, 4))
    for key, label, colour in [("chord_baseline_m", "baseline", "tab:blue"),
                               ("chord_best_found_m", "best found", "tab:orange")]:
        half = np.array(result[key]) / 2
        ax.fill_between(rr, -half, half, alpha=0.3, color=colour, label=label)
        ax.plot(rr, half, color=colour)
        ax.plot(rr, -half, color=colour)
    ax.set_xlabel("radius (m)")
    ax.set_ylabel("chord (m), centred on the blade axis")
    ax.set_title(f"Planform: Cp {result['cp_baseline']} -> {result['cp_best_found']}")
    ax.set_aspect("equal")
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return os.path.abspath(path)


if __name__ == "__main__":
    out = optimize_chord(lambda_design=7, alpha_design_deg=6, num_blades=3, chord_cap_m=2.0)
    for k, v in out.items():
        print(k, v)
    print("saved", plot_planform(out))
