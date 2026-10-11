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
}


def extract_inputs(text):
    t = text.lower().replace("λ", " tsr ").replace("α", " aoa ")

    def grab(patterns, default):
        for p in patterns:
            m = re.search(p, t)
            if m:
                return float(m.group(1))
        return default

    return {
        "lambda_design": grab([r"tip[- ]speed ratio(?: of)? (\d+(?:\.\d+)?)", r"tsr(?: of| =)? ?(\d+(?:\.\d+)?)"], 7.0),
        "alpha_design_deg": grab([r"(\d+(?:\.\d+)?) ?(?:degrees|degree|deg|°)", r"aoa(?: of)? ?(\d+(?:\.\d+)?)"], 6.0),
        "num_blades": int(grab([r"(\d) ?-?blades?", r"(\d)-blade"], 3)),
        "chord_cap_m": grab([r"(\d+(?:\.\d+)?) ?m (?:chord )?cap", r"chord cap(?: of)? (\d+(?:\.\d+)?)", r"cap(?: of)? (\d+(?:\.\d+)?)"], 2.0),
    }


def pick_mock_tools(text):
    t = text.lower()
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
        swept = {
            "sweep_blade_count": "num_blades",
            "sweep_tsr": "lambda_design",
            "sweep_aoa": "alpha_design_deg",
            "sweep_chord_cap": "chord_cap_m",
        }.get(name)
        tool_input = {k: v for k, v in inputs.items() if k != swept}
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
}


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
        if self.path != "/api/chat":
            return self.send_error(404)
        length = int(self.headers.get("Content-Length") or 0)
        if length > 200_000:
            return self.send_json(413, {"error": "Request too large."})
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self.send_json(400, {"error": "Request body is not valid JSON."})

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
