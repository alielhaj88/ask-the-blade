"""Ask the Blade: a local web interface for the BEM solver agent.

Run it:
    python3 app.py              real agent (uses the Anthropic API)
    python3 app.py --mock       no API calls; the solver still runs for real
Options:
    --port 8000                 which port to serve on
    --budget 0.10               spending cap in dollars for this server session
Then open http://localhost:8000
"""
import argparse
import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"


def load_dotenv():
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        os.environ.setdefault(key, value.strip().strip('"').strip("'"))


load_dotenv()
import BEM_solver_agent as agent  # noqa: E402  (imported after the key is loaded)
from bem_core import POLARS  # noqa: E402
from blade_geometry import build_mesh, stl_text  # noqa: E402

PRICE_IN, PRICE_OUT = 1e-6, 5e-6  # Claude Haiku 4.5, dollars per token
MODEL_LABEL = "Claude Haiku 4.5"
MAX_HISTORY_PAIRS = 6

CONFIG = {"mock": False, "budget": 0.10}
SPEND = {"dollars": 0.0}
SPEND_LOCK = threading.Lock()


# ---------- helpers ----------

def parse_output(content):
    try:
        return json.loads(content)
    except (TypeError, ValueError):
        return content


def clean_history(raw):
    """Keep only complete user -> assistant text pairs, newest last."""
    if not isinstance(raw, list):
        return []
    pairs = []
    pending_user = None
    for item in raw:
        if not isinstance(item, dict):
            continue
        role, content = item.get("role"), item.get("content")
        if not isinstance(content, str) or not content.strip():
            pending_user = None if role == "assistant" else pending_user
            continue
        if role == "user":
            pending_user = content[:4000]
        elif role == "assistant" and pending_user is not None:
            pairs.append((pending_user, content[:6000]))
            pending_user = None
    history = []
    for user_text, assistant_text in pairs[-MAX_HISTORY_PAIRS:]:
        history.append({"role": "user", "content": user_text})
        history.append({"role": "assistant", "content": assistant_text})
    return history


def friendly_error(e):
    name = type(e).__name__
    if name == "AuthenticationError":
        return "The API key was rejected. Check ANTHROPIC_API_KEY in your .env file."
    if name == "APIConnectionError":
        return "Could not reach the Anthropic API. Check your internet connection and try again."
    if name == "RateLimitError":
        return "The API is rate limiting requests. Wait a few seconds and try again."
    if name in ("APIStatusError", "InternalServerError", "OverloadedError"):
        return "The API returned an error. Try again in a moment."
    return f"{name}: {e}"


# ---------- mock agent (no API calls, real solver) ----------

AXIS_LABEL = {
    "lambda_design": "tip-speed ratio",
    "alpha_design_deg": "angle of attack",
    "num_blades": "blade count",
    "chord_cap_m": "chord cap",
    "airfoil": "airfoil",
}
SWEPT = {
    "sweep_blade_count": "num_blades",
    "sweep_tsr": "lambda_design",
    "sweep_aoa": "alpha_design_deg",
    "sweep_chord_cap": "chord_cap_m",
    "compare_airfoils": "airfoil",
}


def extract_inputs(text):
    t = text.lower().replace("λ", " tsr ").replace("α", " aoa ")

    def grab(patterns, default):
        for p in patterns:
            m = re.search(p, t)
            if m:
                return float(m.group(1))
        return default

    inputs = {
        "lambda_design": grab([r"tip[- ]speed ratio(?: of)? (\d+(?:\.\d+)?)", r"tsr(?: of| =)? ?(\d+(?:\.\d+)?)"], 7.0),
        "alpha_design_deg": grab([r"(\d+(?:\.\d+)?) ?(?:degrees|degree|deg|°)", r"aoa(?: of)? ?(\d+(?:\.\d+)?)"], 6.0),
        "num_blades": int(grab([r"\b(\d) ?-?blades?", r"\b(\d)-blade"], 3)),
        "chord_cap_m": grab([r"(\d+(?:\.\d+)?) ?m (?:chord )?cap", r"chord cap(?: of)? (\d+(?:\.\d+)?)", r"cap(?: of)? (\d+(?:\.\d+)?)"], 2.0),
    }
    foil = re.search(r"\b(naca ?0012|naca ?4412|naca ?4415|s809)\b", t)
    if foil:
        inputs["airfoil"] = foil.group(1).replace(" ", "")
    return inputs


def extract_limits(text):
    t = text.lower().replace(",", "")
    limits = {}
    kn = re.search(r"(\d+(?:\.\d+)?) ?kn", t)
    n = re.search(r"(\d{5,6}) ?n\b", t)
    area = re.search(r"(\d+(?:\.\d+)?) ?(?:m2|m²|square met)", t)
    if kn:
        limits["max_thrust_N"] = float(kn.group(1)) * 1000
    elif n:
        limits["max_thrust_N"] = float(n.group(1))
    if area:
        limits["max_blade_area_m2"] = float(area.group(1))
    blades = re.search(r"\b(\d) ?-?blades?", t)
    if blades:
        limits["num_blades"] = int(blades.group(1))
    return limits


def pick_mock_tools(text):
    t = text.lower()
    if re.search(r"\bstl\b|3d model|\bcad\b|3d print", t):
        return ["export_blade_stl"]
    if re.search(r"best (overall )?design|best blade you can|overall best|optimi[sz]e the (whole )?design|under .{0,20}(kn|thrust|area)", t):
        return ["optimize_design"]
    if re.search(r"airfoil", t) and re.search(r"compare|which|best|versus|vs", t):
        return ["compare_airfoils"]
    improve = re.search(r"optimi[sz]e|improve|reshape|better", t)
    if improve and (("chord" in t and "twist" in t) or "blade shape" in t or "whole blade" in t):
        return ["optimize_blade"]
    if improve and "twist" in t:
        return ["optimize_twist"]
    if improve and ("chord" in t or "planform" in t):
        return ["optimize_chord"]
    if "matter" in t and "angle" in t and "chord" in t:
        return ["sweep_aoa", "sweep_chord_cap"]
    if re.search(r"blade count|how many blades|which blade|number of blades", t):
        return ["sweep_blade_count"]
    if re.search(r"(best|which|compare).{0,30}(tip[- ]speed|tsr|λ)|(tip[- ]speed|tsr).{0,30}(best|sweep)", t):
        return ["sweep_tsr"]
    if re.search(r"(best|which|compare).{0,30}angle|angle.{0,30}(best|sweep)", t):
        return ["sweep_aoa"]
    if re.search(r"chord cap.{0,30}(matter|best|sweep)|(matter|best|which).{0,30}chord cap", t):
        return ["sweep_chord_cap"]
    return ["design_blade"]


def mock_answer(tool, inputs, result):
    if tool in ("optimize_chord", "optimize_twist", "optimize_blade"):
        what = {"optimize_chord": "chord only", "optimize_twist": "twist only", "optimize_blade": "chord and twist"}[tool]
        return (
            f"Search over {what}, ring by ring. Best found Cp **{result['cp_best_found']}** against a baseline of "
            f"{result['cp_baseline']} (change {result['cp_change']}). Thrust {result['thrust_baseline_N']:,} N → "
            f"{result['thrust_best_found_N']:,} N. Airfoil: {result['airfoil']}."
        )
    if tool == "compare_airfoils":
        rows = "\n".join(f"| {a} | {cp} | {t:,} |" for a, cp, t in zip(result["airfoil"], result["cp"], result["thrust_N"]))
        return (
            f"Best of the airfoils tested: **{result['best_airfoil']}**, Cp **{result['best_cp']}**. "
            f"Stand-in polar for reference: {result['standin_cp_for_reference']}. Data: {result['airfoil_data']}.\n\n"
            f"| Airfoil | Cp | Thrust (N) |\n|---|---|---|\n{rows}"
        )
    if tool == "optimize_design":
        d = result["best_design"]
        lim = result["limits"]
        lim_txt = ", ".join(f"{k} {v:g}" for k, v in lim.items() if v is not None) or "none"
        return (
            f"Best found design: {d['airfoil']}, tip-speed ratio {d['lambda_design']}, {d['alpha_design_deg']}° angle of attack, "
            f"{d['num_blades']} blades, {d['chord_cap_m']} m chord cap. Cp **{result['cp_best_found']}**, thrust "
            f"{result['thrust_N']:,} N, blade area {result['blade_area_m2']} m². Limits: {lim_txt}. "
            f"{result['designs_within_limits']} of {result['designs_screened']} screened designs were within the limits."
        )
    if tool == "export_blade_stl":
        return (
            f"Saved `{result['stl_file']}` ({result['shape']} blade, {result['triangles']} triangles, "
            f"watertight: {'yes' if result['watertight'] else 'no'}, {result['file_size_kB']} kB). "
            f"Open the run card to view it in 3D or download it."
        )
    if tool == "design_blade":
        c, tw = result["chord_m"], result["twist_deg"]
        return (
            f"Cp is **{result['cp']:.4f}** and thrust is **{result['thrust_N']:,.0f} N** "
            f"({result['thrust_N'] / 1000:.1f} kN) at tip-speed ratio {inputs['lambda_design']:g}, "
            f"{inputs['alpha_design_deg']:g}° angle of attack, {inputs['num_blades']} blades and a "
            f"{inputs['chord_cap_m']:g} m chord cap.\n\n"
            f"- Chord: {c[0]} m at the root ring, {c[-1]} m at the tip ring\n"
            f"- Twist: {tw[0]}° at the root ring, {tw[-1]}° at the tip ring\n"
            f"- All rings converged: {'yes' if result['all_rings_converged'] else 'no'}"
        )
    key = next(k for k in AXIS_LABEL if k in result)
    rows = "\n".join(f"| {v:g} | {cp} |" for v, cp in zip(result[key], result["cp"]))
    return (
        f"Best of the values tested: {AXIS_LABEL[key]} **{result['best_' + key]:g}**, "
        f"Cp **{result['best_cp']}**. The Cp spread across the sweep is {result['cp_spread']}.\n\n"
        f"| {AXIS_LABEL[key].capitalize()} | Cp |\n|---|---|\n{rows}"
    )


def run_mock(question, send):
    inputs = extract_inputs(question)
    tools = pick_mock_tools(question)
    parts = []
    for i, name in enumerate(tools):
        swept = SWEPT.get(name)
        if name == "optimize_design":
            tool_input = {**extract_limits(question), **({"airfoil": inputs["airfoil"]} if "airfoil" in inputs else {})}
        else:
            tool_input = {k: v for k, v in inputs.items() if k != swept}
        if name == "export_blade_stl" and re.search(r"best|optimi[sz]ed|improved", question.lower()):
            tool_input["shape"] = "best_found"
        call_id = f"mock_{int(time.time() * 1000)}_{i}"
        send("tool_start", {"id": call_id, "name": name, "input": tool_input})
        time.sleep(0.6)
        try:
            result = agent.TOOLS[name](tool_input)
            send("tool_result", {"id": call_id, "name": name, "input": tool_input, "output": result, "is_error": False})
            parts.append(mock_answer(name, inputs, result))
        except ValueError as e:
            send("tool_result", {"id": call_id, "name": name, "input": tool_input, "output": str(e), "is_error": True})
            parts.append(f"The solver refused this request: {e}.")
    time.sleep(0.3)
    send("answer", {"text": "*Mock mode: no model was called. This summary is generated from the tool results.*\n\n" + "\n\n".join(parts)})


# ---------- real agent ----------

def run_real(question, history, send):
    with SPEND_LOCK:
        if SPEND["dollars"] >= CONFIG["budget"]:
            send("error", {"message": f"The ${CONFIG['budget']:.2f} spending cap for this server session is reached. "
                                      "Restart the server to reset it, or start it with a higher --budget."})
            return

    def on_event(kind, data):
        if kind == "usage":
            cost = data["in"] * PRICE_IN + data["out"] * PRICE_OUT
            with SPEND_LOCK:
                SPEND["dollars"] += cost
                spent = SPEND["dollars"]
            send("usage", {**data, "cost": cost, "session_spent": spent, "budget": CONFIG["budget"]})
        elif kind == "tool_result":
            send("tool_result", {**data, "output": parse_output(data["output"])})
        else:
            send(kind, data)

    answer, _, _ = agent.ask_agent(question, history, on_event)
    if answer is None:
        send("error", {"message": "The agent used all 5 steps without finishing. Try a narrower question."})
    elif not answer.strip():
        send("error", {"message": "The agent returned an empty answer. Try rephrasing the question."})
    else:
        send("answer", {"text": answer})


# ---------- HTTP ----------

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".stl": "model/stl",
}


def geometry_from_body(body):
    """Check a blade shape sent by the page: ten radii, chords and twists, plus an airfoil name."""
    radius, chord, twist = body.get("radius_m"), body.get("chord_m"), body.get("twist_deg")
    foil = body.get("airfoil", "standin")
    for name, values, low, high in (("radius_m", radius, 2, 20), ("chord_m", chord, 0.01, 3.5), ("twist_deg", twist, -90, 90)):
        if not isinstance(values, list) or len(values) != 10 or not all(isinstance(v, (int, float)) for v in values):
            raise ValueError(f"{name} must be a list of 10 numbers")
        if not all(low <= v <= high for v in values):
            raise ValueError(f"{name} values must be between {low} and {high}")
    if foil not in POLARS:
        raise ValueError(f"airfoil must be one of {POLARS}")
    return build_mesh(radius, chord, twist, foil)


class Handler(BaseHTTPRequestHandler):
    server_version = "AskTheBlade/1.0"

    def log_message(self, fmt, *args):
        if self.path.startswith("/api/"):
            print(f"  {self.command} {self.path}")

    def send_json(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/api/status":
            with SPEND_LOCK:
                spent = SPEND["dollars"]
            return self.send_json(200, {"mock": CONFIG["mock"], "model": MODEL_LABEL,
                                        "budget": CONFIG["budget"], "spent": spent})
        if path.startswith("/exports/"):
            exports = Path(agent.EXPORT_DIR).resolve()
            file = (exports / path[len("/exports/"):]).resolve()
            if exports not in file.parents or not file.is_file() or file.suffix != ".stl":
                return self.send_error(404)
            data = file.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "model/stl")
            self.send_header("Content-Disposition", f'attachment; filename="{file.name}"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            return self.wfile.write(data)
        if path == "/":
            path = "/index.html"
        file = (WEB / path.lstrip("/")).resolve()
        if WEB.resolve() not in file.parents or not file.is_file():
            return self.send_error(404)
        data = file.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", CONTENT_TYPES.get(file.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if self.path not in ("/api/chat", "/api/mesh", "/api/stl"):
            return self.send_error(404)
        length = int(self.headers.get("Content-Length") or 0)
        if length > 200_000:
            return self.send_json(413, {"error": "Request too large."})
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self.send_json(400, {"error": "Request body is not valid JSON."})

        if self.path in ("/api/mesh", "/api/stl"):
            try:
                verts, faces = geometry_from_body(body)
            except (ValueError, TypeError) as e:
                return self.send_json(400, {"error": str(e)})
            if self.path == "/api/mesh":
                return self.send_json(200, {"positions": verts.round(4).ravel().tolist(),
                                            "indices": faces.ravel().tolist()})
            data = stl_text(verts, faces).encode()
            self.send_response(200)
            self.send_header("Content-Type", "model/stl")
            self.send_header("Content-Disposition", 'attachment; filename="blade.stl"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            return self.wfile.write(data)

        question = str(body.get("message", "")).strip()[:4000]
        history = clean_history(body.get("history"))

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        def send(kind, data):
            try:
                self.wfile.write(f"event: {kind}\ndata: {json.dumps(data)}\n\n".encode())
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

        started = time.time()
        try:
            if not question:
                send("error", {"message": "Type a question first."})
            elif CONFIG["mock"]:
                run_mock(question, send)
            else:
                run_real(question, history, send)
        except Exception as e:
            send("error", {"message": friendly_error(e)})
        send("done", {"seconds": round(time.time() - started, 1)})


def main():
    parser = argparse.ArgumentParser(description="Ask the Blade web interface")
    parser.add_argument("--mock", action="store_true", help="no API calls; the solver still runs")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)))
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    parser.add_argument("--budget", type=float, default=0.10, help="spending cap in dollars for this session")
    args = parser.parse_args()
    CONFIG["mock"], CONFIG["budget"] = args.mock, args.budget

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    mode = "MOCK (no API calls)" if args.mock else f"LIVE ({MODEL_LABEL}, cap ${args.budget:.2f})"
    print(f"\n  Ask the Blade  ·  {mode}\n  Open http://localhost:{args.port}\n  Press Ctrl+C to stop.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.")
        server.server_close()


if __name__ == "__main__":
    main()
