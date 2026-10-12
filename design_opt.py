"""
design_opt.py -- Phase 11: multi-variable optimisation.

Two levels:
1. optimize_blade: for ONE design, search chord and twist together, ring by ring
   (every ring is independent in BEM, so a grid per ring finds the best pair).
2. optimize_design: search the design inputs themselves (tip-speed ratio, angle of
   attack, blade count, chord cap, airfoil), with optional limits on thrust and
   blade area, then refine the top candidates with optimize_blade.
"""
import io
from contextlib import redirect_stdout

import numpy as np
from bem_core import design_blade, solve_rotor, POLARS, V_inf, R, r, dr, N

C_MIN, C_STEP = 0.1, 0.1                         # m
OFFSET_MIN, OFFSET_MAX, OFFSET_STEP = -12.0, 6.0, 0.5   # degrees of extra twist

# Screening grid for optimize_design
LAMBDAS = [4, 5, 6, 7, 8, 9, 10]
ALPHAS = [2, 4, 6, 8, 10, 12]
BLADES = [1, 2, 3, 4]
CAPS = [1.0, 1.5, 2.0, 2.5, 3.0]


def _quiet_solve(omega, c, beta, B, airfoil):
    with redirect_stdout(io.StringIO()), np.errstate(all="ignore"):
        return solve_rotor(omega, c, beta, B, airfoil)


def blade_area_m2(chord):
    """Planform area of ONE blade: sum of chord times ring width."""
    return float(np.sum(chord) * dr)


def optimize_blade(lambda_design, alpha_design_deg, num_blades, chord_cap_m, airfoil="standin", a_target=1/3):
    """Search chord AND twist per ring. Returns baseline and best-found blade."""
    omega = lambda_design * V_inf / R
    c_base, beta_base = design_blade(omega, alpha_design_deg, a_target, chord_cap_m, num_blades, airfoil)
    base = _quiet_solve(omega, c_base, beta_base, num_blades, airfoil)

    best_power = np.where(base["converged"], base["dQ"] * omega, -np.inf)
    best_c, best_beta = c_base.copy(), beta_base.copy()

    chords = np.arange(C_MIN, chord_cap_m + 1e-9, C_STEP)
    offsets = np.radians(np.arange(OFFSET_MIN, OFFSET_MAX + 1e-9, OFFSET_STEP))
    for c in chords:                     # every (chord, twist offset) pair, all rings at once
        for off in offsets:
            res = _quiet_solve(omega, np.full(N, c), beta_base + off, num_blades, airfoil)
            ring_power = res["dQ"] * omega
            better = (ring_power > best_power) & res["converged"]
            best_power[better] = ring_power[better]
            best_c[better] = c
            best_beta[better] = beta_base[better] + off

    best = _quiet_solve(omega, best_c, best_beta, num_blades, airfoil)
    return {
        "cp_baseline": round(float(base["Cp"]), 4),
        "cp_best_found": round(float(best["Cp"]), 4),
        "cp_change": round(float(best["Cp"] - base["Cp"]), 4),
        "thrust_baseline_N": round(float(base["T"])),
        "thrust_best_found_N": round(float(best["T"])),
        "blade_area_baseline_m2": round(blade_area_m2(c_base), 2),
        "blade_area_best_found_m2": round(blade_area_m2(best_c), 2),
        "chord_baseline_m": c_base.round(3).tolist(),
        "chord_best_found_m": best_c.round(3).tolist(),
        "twist_baseline_deg": np.degrees(beta_base).round(2).tolist(),
        "twist_best_found_deg": np.degrees(best_beta).round(2).tolist(),
        "radius_m": r.round(2).tolist(),
        "airfoil": airfoil,
        "baseline_all_rings_converged": bool(base["converged"].all()),
        "all_rings_converged": bool(best["converged"].all()),
    }


def optimize_design(airfoil="any", num_blades=None, max_thrust_N=None, max_blade_area_m2=None, top_k=3):
    """Search design inputs on a grid, keep designs inside the limits, refine the best few."""
    airfoils = POLARS if airfoil == "any" else [airfoil]
    blade_counts = BLADES if num_blades is None else [int(num_blades)]

    def fits(thrust, area):
        return ((max_thrust_N is None or thrust <= max_thrust_N) and
                (max_blade_area_m2 is None or area <= max_blade_area_m2))

    screened, candidates = 0, []
    for foil in airfoils:
        for lam in LAMBDAS:
            omega = lam * V_inf / R
            for alpha in ALPHAS:
                for B in blade_counts:
                    for cap in CAPS:
                        screened += 1
                        c, beta = design_blade(omega, alpha, 1/3, cap, B, foil)
                        res = _quiet_solve(omega, c, beta, B, foil)
                        if not res["converged"].all() or not np.isfinite(res["Cp"]):
                            continue
                        area = blade_area_m2(c)
                        if fits(res["T"], area):
                            candidates.append({"airfoil": foil, "lambda_design": lam, "alpha_design_deg": alpha,
                                               "num_blades": B, "chord_cap_m": cap,
                                               "cp": float(res["Cp"]), "thrust_N": float(res["T"]),
                                               "blade_area_m2": area})
    if not candidates:
        raise ValueError("no design on the search grid meets these limits; loosen max_thrust_N or max_blade_area_m2")

    candidates.sort(key=lambda d: d["cp"], reverse=True)
    best = None
    for cand in candidates[:top_k]:      # refine the best few with the chord+twist search
        refined = optimize_blade(cand["lambda_design"], cand["alpha_design_deg"], cand["num_blades"],
                                 cand["chord_cap_m"], cand["airfoil"])
        use_refined = refined["all_rings_converged"] and fits(refined["thrust_best_found_N"],
                                                              refined["blade_area_best_found_m2"])
        cp = refined["cp_best_found"] if use_refined else round(cand["cp"], 4)
        if best is None or cp > best["cp"]:
            best = {"cand": cand, "refined": refined, "use_refined": use_refined, "cp": cp}

    cand, refined = best["cand"], best["refined"]
    k = "best_found" if best["use_refined"] else "baseline"
    return {
        "best_design": {key: cand[key] for key in ("airfoil", "lambda_design", "alpha_design_deg", "num_blades", "chord_cap_m")},
        "cp_screening": round(cand["cp"], 4),
        "cp_best_found": best["cp"],
        "chord_twist_search_used": best["use_refined"],
        "thrust_N": refined[f"thrust_{k}_N"],
        "blade_area_m2": refined[f"blade_area_{k}_m2"],
        "chord_m": refined[f"chord_{k}_m"],
        "twist_deg": refined[f"twist_{k}_deg"],
        "radius_m": refined["radius_m"],
        "limits": {"max_thrust_N": max_thrust_N, "max_blade_area_m2": max_blade_area_m2},
        "designs_screened": screened,
        "designs_within_limits": len(candidates),
        "runners_up": [{key: (round(d[key], 4) if key == "cp" else d[key])
                        for key in ("airfoil", "lambda_design", "alpha_design_deg", "num_blades", "chord_cap_m", "cp")}
                       for d in candidates[1:4]],
    }


if __name__ == "__main__":
    import json, time
    t = time.time()
    out = optimize_blade(7, 6, 3, 2.0)
    print("optimize_blade standin:", out["cp_baseline"], "->", out["cp_best_found"], f"({time.time() - t:.1f}s)")
    t = time.time()
    out = optimize_design(max_thrust_N=45000, max_blade_area_m2=25)
    print(json.dumps({k: v for k, v in out.items() if k not in ("chord_m", "twist_deg", "radius_m")}, indent=1))
    print(f"({time.time() - t:.1f}s)")
