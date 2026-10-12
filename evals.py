"""
evals.py -- automatic evals that cost nothing (no API calls).

They check the solver, the tools and the geometry against facts that must hold,
so a code change that breaks physics or a tool shows up straight away.
Run:  python3 evals.py
"""
import json
import sys
import time

import numpy as np

import BEM_solver_agent as agent
from bem_core import POLARS, airfoil, design_blade, solve_rotor
from blade_geometry import build_mesh, is_watertight, signed_volume

BETZ = 16 / 27
BASE = {"lambda_design": 7, "alpha_design_deg": 6, "num_blades": 3, "chord_cap_m": 2.0}
RESULTS = []


def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition), detail))


def run_evals():
    # ---- 1. regression: the verified numbers from earlier phases ----
    out = agent.TOOLS["design_blade"](BASE)
    check("baseline Cp is 0.4978", round(out["cp"], 4) == 0.4978, out["cp"])
    check("baseline thrust is 42,531 N", round(out["thrust_N"]) == 42531, out["thrust_N"])
    sweep = agent.TOOLS["sweep_blade_count"]({k: v for k, v in BASE.items() if k != "num_blades"})
    check("blade count sweep matches", sweep["cp"] == [0.4071, 0.4878, 0.4978, 0.5], sweep["cp"])
    sweep = agent.TOOLS["sweep_aoa"]({k: v for k, v in BASE.items() if k != "alpha_design_deg"})
    check("angle of attack sweep matches", sweep["cp"] == [0.4681, 0.4906, 0.4978, 0.498, 0.4948, 0.4896], sweep["cp"])

    # ---- 2. physics on every airfoil ----
    for foil in POLARS:
        omega = 7 * 8 / 20
        c, beta = design_blade(omega, 6, 1/3, 2.0, 3, foil)
        res = solve_rotor(omega, c, beta, 3, foil)
        check(f"{foil}: Cp below Betz", res["Cp"] < BETZ, round(float(res["Cp"]), 4))
        check(f"{foil}: all rings converge", res["converged"].all())
        gap = abs(res["T"] - res["T_mom"]) / res["T_mom"] * 100
        check(f"{foil}: BEM and momentum thrust agree within 1%", gap < 1.0, f"{gap:.3f}%")
        check(f"{foil}: a between 0 and 0.5", np.all((res["a"] >= 0) & (res["a"] <= 0.5)))
        check(f"{foil}: twist decreases root to tip", np.all(np.diff(beta) < 0))
        cl, cd = airfoil(np.radians(np.linspace(-80, 80, 161)), foil)
        check(f"{foil}: polar finite from -80 to 80 deg", np.all(np.isfinite(cl)) and np.all(np.isfinite(cd)))
        check(f"{foil}: drag positive", np.all(cd > 0))

    # ---- 3. search tools never lose to their own baseline ----
    for name in ("optimize_chord", "optimize_twist", "optimize_blade"):
        for foil in ("standin", "naca4412"):
            o = agent.TOOLS[name]({**BASE, "airfoil": foil})
            check(f"{name} ({foil}): best found >= baseline", o["cp_best_found"] >= o["cp_baseline"],
                  f"{o['cp_baseline']} -> {o['cp_best_found']}")
            check(f"{name} ({foil}): below Betz", o["cp_best_found"] < BETZ)
            check(f"{name} ({foil}): all rings converge", o["all_rings_converged"])

    o = agent.TOOLS["optimize_chord"](BASE)
    check("optimize_chord respects the cap", max(o["chord_best_found_m"]) <= BASE["chord_cap_m"] + 1e-9)

    # ---- 4. comparison and multi-variable search ----
    o = agent.TOOLS["compare_airfoils"](BASE)
    check("compare_airfoils covers 4 real airfoils", o["airfoil"] == [p for p in POLARS if p != "standin"])
    check("compare_airfoils best is the max Cp", o["best_cp"] == max(o["cp"]))

    t = time.time()
    o = agent.TOOLS["optimize_design"]({"num_blades": 3, "max_thrust_N": 40000, "max_blade_area_m2": 25})
    check("optimize_design meets thrust limit", o["thrust_N"] <= 40000, o["thrust_N"])
    check("optimize_design meets area limit", o["blade_area_m2"] <= 25, o["blade_area_m2"])
    check("optimize_design kept blade count", o["best_design"]["num_blades"] == 3)
    check("optimize_design finishes in under 30 s", time.time() - t < 30, f"{time.time() - t:.1f}s")
    check("optimize_design result fits in a tool result", len(json.dumps(o)) < agent.MAX_RESULT_CHARS)

    # ---- 5. geometry ----
    for foil in POLARS:
        out = agent.TOOLS["design_blade"]({**BASE, "airfoil": foil})
        v, f = build_mesh(out["radius_m"], out["chord_m"], out["twist_deg"], foil)
        check(f"{foil}: mesh is watertight", is_watertight(f))
        check(f"{foil}: mesh volume positive", signed_volume(v, f) > 0)
        check(f"{foil}: mesh spans hub to tip", np.isclose(v[:, 1].min(), 2.0) and np.isclose(v[:, 1].max(), 20.0))
    o = agent.TOOLS["export_blade_stl"](BASE)
    with open(o["stl_file"]) as fh:
        facets = sum(1 for line in fh if line.lstrip().startswith("facet"))
    check("STL file has every triangle", facets == o["triangles"], f"{facets} facets")

    # ---- 6. guards the agent relies on ----
    bad_inputs = [
        ("design_blade", {**BASE, "lambda_design": 15}),
        ("design_blade", {**BASE, "airfoil": "clarky"}),
        ("design_blade", {"lambda_design": 7}),
        ("optimize_design", {"max_thrust_N": 1}),
        ("export_blade_stl", {**BASE, "shape": "round"}),
    ]
    for name, inp in bad_inputs:
        try:
            agent.TOOLS[name](inp)
            check(f"{name} refuses {inp}", False, "no error raised")
        except ValueError:
            check(f"{name} refuses bad input", True)
    names = [t["name"] for t in agent.ALL_TOOLS]
    check("every schema has a function in TOOLS", sorted(names) == sorted(agent.TOOLS))
    for t in agent.ALL_TOOLS:
        props = t["input_schema"]["properties"]
        check(f"{t['name']}: required keys exist", all(k in props for k in t["input_schema"]["required"]))
        check(f"{t['name']}: every input has a description", all("description" in p for p in props.values()))


if __name__ == "__main__":
    start = time.time()
    run_evals()
    failed = [r for r in RESULTS if not r[1]]
    for name, ok, detail in RESULTS:
        if not ok or "-v" in sys.argv:
            print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")
    print(f"\n{len(RESULTS) - len(failed)} of {len(RESULTS)} evals pass ({time.time() - start:.1f}s)")
    sys.exit(1 if failed else 0)
