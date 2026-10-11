import anthropic
from bem_core import design_blade, solve_rotor, V_inf, R, r
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
            "required": list(properties),
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

RANGES = {
    "lambda_design": (4, 10),
    "alpha_design_deg": (2, 12),
    "num_blades": (1, 4),
    "chord_cap_m": (1, 3),
}

def run_bem(tool_input):
    for name, (low, high) in RANGES.items():
        value = tool_input[name]
        if not low <= value <= high:
            raise ValueError(f"{name} must be between {low} and {high}, got {value}")
    
    num_blades = tool_input['num_blades']
    omega_design = tool_input['lambda_design'] * V_inf / R

    chord, twist = design_blade(
        omega_design=omega_design,
        alpha_d_deg=tool_input['alpha_design_deg'],
        a_target=1/3,
        c_max=tool_input['chord_cap_m'],
        B=num_blades,
    )
    res = solve_rotor(omega_design, chord, twist, num_blades)

    return {
        "cp": float(res['Cp']),
        "thrust_N": res['T'],
        "chord_m": chord.round(3).tolist(),
        "radius_m": r.round(2).tolist(),
        "twist_deg": np.degrees(twist).round(2).tolist(),
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
}

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
- Sweep results: "best" means the highest Cp among the values tested, nothing more. Say "best of the values tested". Never say optimal, optimized, peak, sweet spot, well-designed, efficient, excellent, good, realistic, feasible or stable.
- Each sweep changes one input and holds the others fixed. State the fixed values you used. Never say what Cp a combination of settings would give unless a tool ran that exact combination.
- For convergence, only say whether the tool reported that all rings converged.
- For blade shape, only quote values from the tool result, for example the first and last chord or twist. Do not describe trends, shapes or patterns (no "linear", "increasing", "tapering smoothly", "classic").
- Do not explain why a result happens, and do not discuss cost, weight, noise, structure, manufacturing, maintenance or real-world practice. If asked why or about trade-offs, say the solver does not evaluate that and give the numbers it does have.

WHEN NOT TO CALL A TOOL
- The valid ranges are: tip-speed ratio 4 to 10, design angle of attack 2 to 12 degrees, 1 to 4 blades, chord cap 1.0 to 3.0 m. If the user asks for a value outside a range, do not call a tool. Say which input is out of range and what the range is.
- If the question is missing an input a tool needs (tip-speed ratio, angle of attack, blade count or chord cap), ask the user for it. Do not assume defaults.
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
            tools=[design_blade_tool, sweep_blade_count_tool, sweep_tsr_tool, sweep_aoa_tool, sweep_chord_cap_tool],
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