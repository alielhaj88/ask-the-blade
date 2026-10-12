import anthropic
import os
from bem_core import design_blade, solve_rotor, V_inf, R, r, POLARS
from chord_opt import optimize_chord
from twist_opt import optimize_twist
from design_opt import optimize_blade, optimize_design
from blade_geometry import build_mesh, write_stl, is_watertight, signed_volume
import numpy as np
import json
from datetime import datetime

client = anthropic.Anthropic()

DEFAULT_QUESTION = "At tip-speed ratio 7 with 3 blades, angle of attack 6 degrees and chord cap 2.0 m, which matters more for Cp: changing the design angle of attack or changing the chord cap?"

design_blade_tool = {
    "name": "design_blade",
    "description": "Designs a wind turbine blade (chord and twist along the span) for a given tip-speed ratio, design angle of attack, blade count and chord cap. Validated ranges only.",
    "input_schema": 
    {
        "type": "object",
        "properties": 
        {
            "lambda_design": {
                "type": "number",
                "description": "Design tip-speed ratio. Valid range: 4 to 10."
            },
            "alpha_design_deg": {
                "type": "number",
                "description": "Angle of attack the blade is designed around, in degrees, the angle between the chord line and the oncoming relative wind. Valid range: 2 to 12."
            },
            "num_blades": {
                "type": "integer",
                "description": "Number of blades. Valid range: 1 to 4."
            },
            "chord_cap_m": {
                "type": "number",
                "description": "Maximum allowed chord length at any point along the blade, in meters. The design never exceeds this, even if the theoretical optimum would be wider. Valid range: 1.0 to 3.0."
            },
            "airfoil": {
                "type": "string",
                "enum": POLARS,
                "description": "Airfoil polar. 'standin' (default) is a made-up teaching polar, not real data. naca0012, naca4412, naca4415 and s809 are XFOIL data at Reynolds number 1,000,000. Leave it out unless the user names an airfoil or asks for real airfoil data."
            },
        },
        "required": ["lambda_design", "alpha_design_deg", "num_blades", "chord_cap_m"]
    },
}

def make_sweep_tool(name, description, swept_key):
    properties = {k: v for k, v in design_blade_tool["input_schema"]["properties"].items() if k != swept_key}
    return {
        "name": name,
        "description": description,
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": [k for k in design_blade_tool["input_schema"]["required"] if k != swept_key],
        },
    }

sweep_blade_count_tool = make_sweep_tool(
    "sweep_blade_count",
    "Tries blade counts 1 to 4 at one fixed design (tip-speed ratio, angle of attack, chord cap). Use this when the question compares blade counts or asks which count is best. Returns the Cp for each blade count, whether each design converged, and the best blade count.",
    "num_blades",
)

sweep_tsr_tool = make_sweep_tool(
    "sweep_tsr",
    "Designs a separate blade at each tip-speed ratio from 4 to 10 and compares them at fixed blade count, angle of attack and chord cap. Use this when the question compares tip-speed ratios or asks which is best. Returns the Cp for each tip-speed ratio, whether each design converged, and the best one.",
    "lambda_design",
)

sweep_aoa_tool = make_sweep_tool(
    "sweep_aoa",
    "Tries design angles of attack from 2 to 12 degrees at fixed tip-speed ratio, blade count and chord cap. Use this when the question compares angles of attack or asks which is best. Returns the Cp for each angle, whether each design converged, and the best one.",
    "alpha_design_deg",
)

sweep_chord_cap_tool = make_sweep_tool(
    "sweep_chord_cap",
    "Tries chord caps from 1.0 to 3.0 m at fixed tip-speed ratio, angle of attack and blade count. Use this when the question compares chord caps or asks how much blade width matters. Returns the Cp for each cap, whether each design converged, and the best one.",
    "chord_cap_m",
)

optimize_chord_tool = {
    "name": "optimize_chord",
    "description": "Searches for a better chord distribution for one design, ring by ring, keeping the baseline twist and the chord cap. Use this when the question asks to improve, optimise or reshape the chord or planform. Returns the baseline Cp and the best-found Cp, cp_change, thrust for both, the chord of each ring for both, and how many rings sit at the chord cap.",
    "input_schema": design_blade_tool["input_schema"],
}

optimize_twist_tool = {
    "name": "optimize_twist",
    "description": "Searches for a better twist distribution for one design, ring by ring, keeping the baseline chord. Use this when the question asks to improve or optimise the twist. Returns baseline and best-found Cp, cp_change, thrust for both, the twist of each ring for both, the twist change per ring, and how many rings hit the edge of the search range.",
    "input_schema": design_blade_tool["input_schema"],
}

optimize_blade_tool = {
    "name": "optimize_blade",
    "description": "Searches chord and twist together for one design, ring by ring. Use this when the question asks to improve the whole blade shape, or both chord and twist. Returns baseline and best-found Cp, thrust and blade area, and the chord and twist of each ring for both.",
    "input_schema": design_blade_tool["input_schema"],
}

compare_airfoils_tool = make_sweep_tool(
    "compare_airfoils",
    "Designs the same blade with each real airfoil (naca0012, naca4412, naca4415, s809; XFOIL data at Reynolds number 1,000,000) and compares Cp and thrust. Use this when the question compares airfoils or asks which airfoil is best. Also returns the stand-in polar's Cp for reference.",
    "airfoil",
)

optimize_design_tool = {
    "name": "optimize_design",
    "description": "Multi-variable search: tries every combination of tip-speed ratio (4 to 10), design angle of attack (2 to 12 degrees), blade count (1 to 4), chord cap (1.0 to 3.0 m) and airfoil on a grid, keeps designs within the optional thrust and blade-area limits, then refines the best few with a chord and twist search. Use this when the user asks for the best overall design, possibly under limits. Takes about 15 seconds.",
    "input_schema": {
        "type": "object",
        "properties": {
            "airfoil": {"type": "string", "enum": ["any"] + POLARS,
                        "description": "Airfoil to use, or 'any' to search all of them. Default 'any'."},
            "num_blades": {"type": "integer", "description": "Fix the blade count (1 to 4). Leave out to search all."},
            "max_thrust_N": {"type": "number", "description": "Optional limit on rotor thrust in newtons. Valid range: 10000 to 100000."},
            "max_blade_area_m2": {"type": "number", "description": "Optional limit on the planform area of one blade in square metres. Valid range: 5 to 60."},
        },
        "required": [],
    },
}

export_blade_stl_tool = {
    "name": "export_blade_stl",
    "description": "Builds the 3D blade for one design and saves it as an STL file (units metres) for CAD or 3D printing. Use this when the user asks for a 3D model, STL or CAD file. 'shape' picks the baseline blade or the best-found blade from the chord and twist search.",
    "input_schema": {
        "type": "object",
        "properties": {
            **design_blade_tool["input_schema"]["properties"],
            "shape": {"type": "string", "enum": ["baseline", "best_found"],
                      "description": "baseline (default) or best_found."},
        },
        "required": design_blade_tool["input_schema"]["required"],
    },
}

RANGES = {
    "lambda_design": (4, 10),
    "alpha_design_deg": (2, 12),
    "num_blades": (1, 4),
    "chord_cap_m": (1, 3),
}
LIMIT_RANGES = {
    "max_thrust_N": (10000, 100000),
    "max_blade_area_m2": (5, 60),
}
EXPORT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "exports")

def check_inputs(tool_input, keys=RANGES):
    """Raise ValueError for a missing or out-of-range input. Returns the airfoil name."""
    for name, (low, high) in keys.items():
        if name not in tool_input:
            raise ValueError(f"{name} is required")
        value = tool_input[name]
        if not low <= value <= high:
            raise ValueError(f"{name} must be between {low} and {high}, got {value}")
    airfoil = tool_input.get("airfoil", "standin")
    if airfoil not in POLARS:
        raise ValueError(f"airfoil must be one of {POLARS}, got {airfoil}")
    return airfoil

def run_bem(tool_input):
    airfoil = check_inputs(tool_input)
    num_blades = tool_input['num_blades']
    omega_design = tool_input['lambda_design'] * V_inf / R

    chord, twist = design_blade(
        omega_design=omega_design,
        alpha_d_deg=tool_input['alpha_design_deg'],
        a_target=1/3,
        c_max=tool_input['chord_cap_m'],
        B=num_blades,
        polar=airfoil,
    )
    res = solve_rotor(omega_design, chord, twist, num_blades, airfoil)

    return {
        "cp": float(res['Cp']),
        "thrust_N": float(res['T']),
        "chord_m": chord.round(3).tolist(),
        "radius_m": r.round(2).tolist(),
        "twist_deg": np.degrees(twist).round(2).tolist(),
        "airfoil": airfoil,
        "all_rings_converged": bool(res['converged'].all()),
    }

def run_sweep(tool_input, swept_key, values):
    cp_list = []
    converged_list = []
    for v in values:
        out = run_bem({**tool_input, swept_key: v})
        cp_list.append(round(out["cp"], 4))
        converged_list.append(out["all_rings_converged"])

    valid = [(cp, v) for cp, v, ok in zip(cp_list, values, converged_list) if ok]
    if not valid:
        raise ValueError(f"no value of {swept_key} converged for these inputs")
    best_cp, best_v = max(valid)

    return {
        swept_key: values,
        "cp": cp_list,
        "all_rings_converged": converged_list,
        "best_" + swept_key: best_v,
        "best_cp": round(best_cp, 4),
        "cp_spread": round(max(cp_list) - min(cp_list), 4),
    }

def design_args(tool_input):
    """The five design inputs as keyword arguments for the optimisers, after checking them."""
    return {
        "lambda_design": tool_input["lambda_design"],
        "alpha_design_deg": tool_input["alpha_design_deg"],
        "num_blades": tool_input["num_blades"],
        "chord_cap_m": tool_input["chord_cap_m"],
        "airfoil": check_inputs(tool_input),
    }

def run_optimize_chord(tool_input):
    return optimize_chord(**design_args(tool_input))

def run_optimize_twist(tool_input):
    return optimize_twist(**design_args(tool_input))

def run_optimize_blade(tool_input):
    return optimize_blade(**design_args(tool_input))

def compare_airfoils(tool_input):
    check_inputs({**tool_input, "airfoil": "standin"})
    real = [p for p in POLARS if p != "standin"]
    rows = [run_bem({**tool_input, "airfoil": p}) for p in real]
    cps = [round(row["cp"], 4) for row in rows]
    ok = [row["all_rings_converged"] for row in rows]
    valid = [(cp, name) for cp, name, good in zip(cps, real, ok) if good]
    if not valid:
        raise ValueError("no airfoil converged for these inputs")
    best_cp, best_name = max(valid)
    return {
        "airfoil": real,
        "cp": cps,
        "thrust_N": [round(row["thrust_N"]) for row in rows],
        "all_rings_converged": ok,
        "best_airfoil": best_name,
        "best_cp": best_cp,
        "cp_spread": round(max(cps) - min(cps), 4),
        "standin_cp_for_reference": round(run_bem({**tool_input, "airfoil": "standin"})["cp"], 4),
        "airfoil_data": "XFOIL polars at Reynolds number 1000000 from airfoiltools.com",
    }

def run_optimize_design(tool_input):
    airfoil = tool_input.get("airfoil", "any")
    if airfoil != "any" and airfoil not in POLARS:
        raise ValueError(f"airfoil must be 'any' or one of {POLARS}, got {airfoil}")
    num_blades = tool_input.get("num_blades")
    if num_blades is not None and not 1 <= num_blades <= 4:
        raise ValueError(f"num_blades must be between 1 and 4, got {num_blades}")
    limits = {k: tool_input[k] for k in LIMIT_RANGES if tool_input.get(k) is not None}
    check_inputs(limits, {k: LIMIT_RANGES[k] for k in limits})
    return optimize_design(airfoil=airfoil, num_blades=num_blades, **limits)

def export_blade_stl(tool_input):
    args = design_args(tool_input)
    shape = tool_input.get("shape", "baseline")
    if shape == "best_found":
        res = optimize_blade(**args)
        chord, twist = res["chord_best_found_m"], res["twist_best_found_deg"]
        cp = res["cp_best_found"]
    elif shape == "baseline":
        res = run_bem(tool_input)
        chord, twist, cp = res["chord_m"], res["twist_deg"], res["cp"]
    else:
        raise ValueError(f"shape must be 'baseline' or 'best_found', got {shape}")

    verts, faces = build_mesh(r, chord, twist, args["airfoil"])
    os.makedirs(EXPORT_DIR, exist_ok=True)
    name = (f"blade_{args['airfoil']}_tsr{args['lambda_design']:g}_aoa{args['alpha_design_deg']:g}"
            f"_B{args['num_blades']}_cap{args['chord_cap_m']:g}_{shape}.stl")
    path = write_stl(os.path.join(EXPORT_DIR, name), verts, faces, name="blade")
    return {
        "stl_file": f"exports/{name}",
        "download_url": f"/exports/{name}",
        "shape": shape,
        "cp": round(float(cp), 4),
        "triangles": int(len(faces)),
        "watertight": is_watertight(faces),
        "volume_m3": round(signed_volume(verts, faces), 3),
        "file_size_kB": round(os.path.getsize(path) / 1024, 1),
        "chord_m": chord,
        "twist_deg": twist,
        "radius_m": r.round(2).tolist(),
        "airfoil": args["airfoil"],
        "outline_note": "standin is drawn with a NACA 4412 outline" if args["airfoil"] == "standin" else "outline from airfoiltools.com",
    }

def sweep_blade_count(tool_input):
    return run_sweep(tool_input, "num_blades", [1, 2, 3, 4])

def sweep_tsr(tool_input):
    return run_sweep(tool_input, "lambda_design", [4, 5.5, 7, 8.5, 10])

def sweep_aoa(tool_input):
    return run_sweep(tool_input, "alpha_design_deg", [2, 4, 6, 8, 10, 12])

def sweep_chord_cap(tool_input):
    return run_sweep(tool_input, "chord_cap_m", [1.0, 1.5, 2.0, 2.5, 3.0])

TOOLS = {
    "design_blade": run_bem,
    "sweep_blade_count": sweep_blade_count,
    "sweep_tsr": sweep_tsr,
    "sweep_aoa": sweep_aoa,
    "sweep_chord_cap": sweep_chord_cap,
    "optimize_chord": run_optimize_chord,
    "optimize_twist": run_optimize_twist,
    "optimize_blade": run_optimize_blade,
    "compare_airfoils": compare_airfoils,
    "optimize_design": run_optimize_design,
    "export_blade_stl": export_blade_stl,
}

ALL_TOOLS = [design_blade_tool, sweep_blade_count_tool, sweep_tsr_tool, sweep_aoa_tool, sweep_chord_cap_tool,
             optimize_chord_tool, optimize_twist_tool, optimize_blade_tool, compare_airfoils_tool,
             optimize_design_tool, export_blade_stl_tool]

LOG_FILE = "tool_log.jsonl"

def log_tool_call(name, tool_input, content, is_error):
    entry = {
        "time": datetime.now().isoformat(),
        "tool": name,
        "input": tool_input,
        "is_error": is_error,
        "output": content,
    }
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")

SYSTEM_PROMPT = """You answer questions about wind turbine blade designs using a blade element momentum (BEM) solver that you can only reach through your tools. You do no physics yourself.

NUMBERS
- Every number in your answer must appear in a tool result or in the user's question. Quote tool numbers as given, or rounded to fewer decimals.
- Do not calculate anything: no differences, spans, percentages, relative gains, ratios or averages. To compare two things, put their tool numbers side by side and say which is higher. For "which matters more" questions, compare the cp_spread values the sweep tools return (the larger spread means changing that input moves Cp more over the values tested), quote them, and say the comparison only covers the values tested. The only allowed conversion is moving the decimal point for units (N to kN).
- Use units: Cp has none, thrust in N, chord and radius in m, twist and angle of attack in degrees.

WHAT YOU MAY CLAIM
- Sweep results: "best" means the highest Cp among the values tested, nothing more. Say "best of the values tested". Never say optimal, optimum, optimize, optimized, optimizing (or the -ise spellings), even if the user used the word; say "the search" and "best found" instead. Never say peak, sweet spot, well-designed, efficient, excellent, good, realistic, feasible or stable.
- Each sweep changes one input and holds the others fixed. State the fixed values you used. Never say what Cp a combination of settings would give unless a tool ran that exact combination.
- Search tools (optimize_chord, optimize_twist, optimize_blade, optimize_design): call the result "best found" by the search, never optimal or optimized. Say what was searched: optimize_chord changes chord only, optimize_twist changes twist only, optimize_blade changes both, ring by ring; optimize_design searches a grid of design inputs within any limits given, then the chord and twist. If baseline_all_rings_converged is false, say the baseline did not converge so the comparison is not reliable. If rings_at_search_limit is above 0, say the search hit the edge of its range.
- optimize_design: report best_design, cp_best_found, thrust_N, blade_area_m2 and the limits used. If the user gives no limits, say none were applied.
- export_blade_stl: give the stl_file path, the shape, the triangle count and whether it is watertight. Do not describe how the blade looks.
- For convergence, only say whether the tool reported that all rings converged.
- For blade shape, only quote values from the tool result, for example the first and last chord or twist. Do not describe trends, shapes or patterns (no "linear", "increasing", "tapering smoothly", "classic").
- Do not explain why a result happens, and do not discuss cost, weight, noise, structure, manufacturing, maintenance or real-world practice. If asked why or about trade-offs, say the solver does not evaluate that and give the numbers it does have.

WHEN NOT TO CALL A TOOL
- The valid ranges are: tip-speed ratio 4 to 10, design angle of attack 2 to 12 degrees, 1 to 4 blades, chord cap 1.0 to 3.0 m, thrust limit 10,000 to 100,000 N, blade area limit 5 to 60 m2. If the user asks for a value outside a range, do not call a tool. Say which input is out of range and what the range is.
- If the question is missing an input a tool needs (tip-speed ratio, angle of attack, blade count or chord cap), ask the user for it. Do not assume defaults. optimize_design is the exception: it needs no design inputs, so use it for "best overall design" questions.
AIRFOILS
- Without an airfoil named by the user, tools use 'standin', a made-up teaching polar. Say so when you report results from it if the user asks about real blades.
- naca0012, naca4412, naca4415 and s809 are XFOIL data at Reynolds number 1,000,000, not wind-tunnel data. When you report results that use them, say that once. Do not rank airfoils beyond the tool numbers.
- The solver has no tip-loss or hub-loss correction. If the user asks whether the Cp values are realistic, say the solver does not include tip losses, so its Cp may be higher than a real rotor's.

OUTSIDE FACTS
- The only outside fact you may state is that the Betz limit is 16/27, about 0.593, and only when asked. For anything else not about this solver (weather, typical real-world values, history), say you can only answer using the solver.
- If a tool returns an error, tell the user what the error said and stop. Do not guess the cause.

STYLE
Short and plain. State the result with its numbers, and the inputs you used. No greetings, no praise, no closing advice."""

MAX_RESULT_CHARS = 4000
USAGE = {"in": 0, "out": 0}

def trim_result(content):
    if len(content) <= MAX_RESULT_CHARS:
        return content
    return content[:MAX_RESULT_CHARS] + f"... [truncated, {len(content)} characters in total]"

def ask_agent(question, history=None, on_event=None):
    # history: earlier turns as [{"role": "user"/"assistant", "content": "text"}]
    # on_event(kind, data): optional callback so an interface can show each step live
    emit = on_event or (lambda kind, data: None)
    messages = list(history or []) + [{"role": "user", "content": question}]
    tool_calls = []
    seen = {}

    for step in range(5):
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            system=SYSTEM_PROMPT,
            tools=ALL_TOOLS,
            messages=messages,
        )
        USAGE["in"] += response.usage.input_tokens
        USAGE["out"] += response.usage.output_tokens
        emit("usage", {"in": response.usage.input_tokens, "out": response.usage.output_tokens})
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            break

        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                call_key = (block.name, json.dumps(block.input, sort_keys=True))
                seen[call_key] = seen.get(call_key, 0) + 1
                emit("tool_start", {"id": block.id, "name": block.name, "input": block.input})
                if seen[call_key] > 2:
                    content = "You already made this exact call twice. Stop calling tools and answer with what you have."
                    is_error = True
                else:
                    try:
                        result = TOOLS[block.name](block.input)
                        content = trim_result(json.dumps(result))
                        is_error = False
                    except ValueError as e:
                        content = str(e)
                        is_error = True
                    except Exception as e:
                        content = f"Tool failed with {type(e).__name__}: {e}"
                        is_error = True
                log_tool_call(block.name, block.input, content, is_error)
                emit("tool_result", {"id": block.id, "name": block.name, "input": block.input, "output": content, "is_error": is_error})
                tool_calls.append({"tool": block.name, "input": block.input, "output": content, "is_error": is_error})
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": content,
                    "is_error": is_error,
                })
        messages.append({"role": "user", "content": tool_results})

    if response.stop_reason == "tool_use":
        answer = None
    else:
        answer = "".join(b.text for b in response.content if b.type == "text")
    return answer, tool_calls, messages

if __name__ == "__main__":
    answer, tool_calls, messages = ask_agent(DEFAULT_QUESTION)
    print(messages)
    if answer is None:
        print("Stopped: hit the step limit before the model finished.")
    else:
        print(answer)