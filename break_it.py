import json
import sys
import anthropic
from BEM_solver_agent import make_sweep_tool, design_blade_tool, run_bem

client = anthropic.Anthropic()
MODEL = "claude-haiku-4-5-20251001"
PRICE_IN, PRICE_OUT = 1 / 1_000_000, 5 / 1_000_000
BUDGET = 0.06
spent = 0.0


def call(messages, tools, max_tokens=300):
    global spent
    if spent >= BUDGET:
        raise SystemExit(f"Budget of ${BUDGET} reached, stopping.")
    r = client.messages.create(model=MODEL, max_tokens=max_tokens, tools=tools, messages=messages)
    cost = r.usage.input_tokens * PRICE_IN + r.usage.output_tokens * PRICE_OUT
    spent += cost
    print(f"   [call: {r.usage.input_tokens} in, {r.usage.output_tokens} out, ${cost:.4f}]")
    return r


def final_text(response):
    return "".join(b.text for b in response.content if b.type == "text")


def run_loop(question, tools, handlers, catch=ValueError, max_steps=5, guard_repeats=False):
    messages = [{"role": "user", "content": question}]
    seen = {}
    for step in range(max_steps):
        r = call(messages, tools)
        messages.append({"role": "assistant", "content": r.content})
        if r.stop_reason != "tool_use":
            return "answered", final_text(r), messages
        results = []
        for b in r.content:
            if b.type != "tool_use":
                continue
            print(f"   step {step + 1}: model called {b.name}({json.dumps(b.input)})")
            key = (b.name, json.dumps(b.input, sort_keys=True))
            seen[key] = seen.get(key, 0) + 1
            if guard_repeats and seen[key] > 2:
                content, is_error = "You already made this exact call twice. Stop calling tools and answer with what you have.", True
            else:
                try:
                    content, is_error = json.dumps(handlers[b.name](b.input)), False
                except catch as e:
                    content, is_error = f"{type(e).__name__}: {e}", True
            results.append({"type": "tool_result", "tool_use_id": b.id, "content": content, "is_error": is_error})
        messages.append({"role": "user", "content": results})
    return "hit step cap", None, messages


def experiment_1_vague_description():
    print("\n=== 1. Vague tool descriptions ===")
    tools = [
        make_sweep_tool("tool_a", "Runs an analysis.", "alpha_design_deg"),
        make_sweep_tool("tool_b", "Runs another analysis.", "num_blades"),
    ]
    q = "At tip-speed ratio 7, angle of attack 6 degrees and chord cap 2.0 m, which blade count is best?"
    r = call([{"role": "user", "content": q}], tools)
    for b in r.content:
        if b.type == "tool_use":
            print(f"   model picked {b.name} with {json.dumps(b.input)}")
        elif b.type == "text":
            print(f"   model said: {b.text[:200]}")
    print("   (the right tool is tool_b, the one that sweeps num_blades)")


def experiment_2_tool_throws():
    print("\n=== 2. A tool that throws an unexpected error ===")
    q = "Design a blade for tip-speed ratio 7, 6 degrees angle of attack, 3 blades and a 2.0 m chord cap."

    def broken(tool_input):
        return 1 / 0

    print(" a) loop with `except ValueError` (what the agent has now):")
    try:
        run_loop(q, [design_blade_tool], {"design_blade": broken}, catch=ValueError)
    except Exception as e:
        print(f"   CRASH: {type(e).__name__}: {e}  (the whole agent stopped, no answer for the user)")

    print(" b) loop with `except Exception`:")
    status, text, _ = run_loop(q, [design_blade_tool], {"design_blade": broken}, catch=Exception)
    print(f"   {status}. Model told the user: {text[:250]}")


def experiment_3_huge_result():
    print("\n=== 3. A huge tool result ===")
    q = "Design a blade for tip-speed ratio 7, 6 degrees angle of attack, 3 blades and a 2.0 m chord cap. What Cp does it give?"
    big = {"cp": 0.4978, "debug_trace": [round(0.4 + (i % 97) / 1000, 4) for i in range(1500)]}

    def huge(tool_input):
        return big

    def truncated(tool_input):
        text = json.dumps(big)
        return {"cp": big["cp"], "note": f"debug_trace removed, it was {len(text)} characters"}

    print(" a) full result sent back:")
    status, text, msgs = run_loop(q, [design_blade_tool], {"design_blade": huge})
    print(f"   {status}. Answer: {(text or '')[:150]}")
    print(" b) result trimmed before sending:")
    status, text, msgs = run_loop(q, [design_blade_tool], {"design_blade": truncated})
    print(f"   {status}. Answer: {(text or '')[:150]}")


def experiment_4_no_stop_condition():
    print("\n=== 4. No stop condition ===")
    next_page_tool = {
        "name": "next_page",
        "description": "Returns the next page of the blade report. Always check for more pages.",
        "input_schema": {"type": "object", "properties": {}},
    }
    counter = {"n": 0}

    def next_page(tool_input):
        counter["n"] += 1
        return {"page": counter["n"], "note": "More pages are available. Call next_page again."}

    q = "Read every page of the blade report, then summarise it."
    print(" a) step cap only (max 6 steps):")
    status, text, msgs = run_loop(q, [next_page_tool], {"next_page": next_page}, max_steps=6)
    print(f"   {status} after {counter['n']} tool calls.")

    counter["n"] = 0
    print(" b) step cap plus a repeat guard (same call 3 times gets stopped):")
    status, text, msgs = run_loop(q, [next_page_tool], {"next_page": next_page}, max_steps=6, guard_repeats=True)
    print(f"   {status} after {counter['n']} real tool calls. Answer: {(text or '')[:150]}")


EXPERIMENTS = {
    1: experiment_1_vague_description,
    2: experiment_2_tool_throws,
    3: experiment_3_huge_result,
    4: experiment_4_no_stop_condition,
}

if __name__ == "__main__":
    chosen = [int(a) for a in sys.argv[1:] if a.isdigit()] or list(EXPERIMENTS)
    for n in chosen:
        EXPERIMENTS[n]()
    print(f"\nTotal spent: ${spent:.4f} (budget ${BUDGET})")
