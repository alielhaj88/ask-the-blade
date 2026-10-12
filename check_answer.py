import json
import re
import anthropic

BETZ = 16 / 27
BETZ_FACTS = [0.593, 0.5926, 59.3, 59.26, 16, 27]
TOOL_FACTS = [1, 2, 3, 4, 5.5, 6, 7, 8, 8.5, 10, 12, 1.5, 2.5, 1000000, 0.25, 15, 10000, 100000, 5, 60, 12, 4412, 4415, 809]
ALLOW_LIST = BETZ_FACTS + TOOL_FACTS
JUDGE_MODEL = "claude-sonnet-5-5"
JUDGE_USAGE = {"in": 0, "out": 0}

NUM_RE = re.compile(r"(?<![\w.])\d+(?:\.\d+)?")

PROSE_RULES = [
    ("says 'optimized'", r"\boptimi[sz](ed|ing|ation)\b"),
    ("says 'optimal'", r"\boptimal\b"),
    ("says twist increases or is linear", r"twist[^.\n]{0,60}\b(increas\w*|linear\w*)|\b(increas\w*|linear\w*)[^.\n]{0,20}twist"),
    ("says 'linear'", r"\blinear(ly)?\b"),
    ("percent of a theoretical maximum", r"\d+(\.\d+)?\s?%\s+of\s+(the\s+)?(theoretical|betz|maximum|max)\b"),
    ("percent of the span", r"\d+(\.\d+)?\s?%\s+of\s+(the\s+)?(span|blade|radius|length)\b|\b(first|last|outer|inner)\s+\d+(\.\d+)?\s?%"),
]


def extract_numbers(text):
    text = re.sub(r"(?<=\d),(?=\d{3})", "", text)
    text = re.sub(r"(?m)^\s*\d+[.)]\s+", "", text)
    return list(dict.fromkeys(NUM_RE.findall(text)))


def number_matches(x_str, v):
    x = float(x_str)
    d = len(x_str.split(".")[1]) if "." in x_str else 0
    v = abs(v)
    if d == 0:
        return v == x or round(v) == x or int(v) == x or round(v * 100) == x
    unit = 10 ** -d
    return any(abs(v * scale - x) < unit for scale in (1, 100, 0.001))


def derive_pool(tool_calls):
    pool = set()
    for tc in tool_calls:
        try:
            data = json.loads(tc["output"])
        except ValueError:
            continue
        if not isinstance(data, dict):
            continue
        for key in ("cp", "best_cp", "thrust_N", "cp_baseline", "cp_best_found",
                    "thrust_baseline_N", "thrust_best_found_N"):
            values = data.get(key)
            if values is None:
                continue
            pool.update(values if isinstance(values, list) else [values])
    return list(pool)


def is_derived(x_str, pool):
    for a in pool:
        for b in pool:
            if a == b or b == 0:
                continue
            for value in (a - b, (a - b) / b * 100):
                if number_matches(x_str, value):
                    return True
    return False


def check_numbers(answer, question, tool_calls):
    pool = {float(n) for n in NUM_RE.findall(question)}
    for tc in tool_calls:
        pool.update(float(n) for n in NUM_RE.findall(json.dumps(tc["input"]) + " " + tc["output"]))
    allowed = pool | set(ALLOW_LIST)
    pool_for_derived = derive_pool(tool_calls)

    invented, derived = [], []
    for x_str in extract_numbers(answer):
        if any(number_matches(x_str, v) for v in allowed):
            continue
        if is_derived(x_str, pool_for_derived):
            derived.append(x_str)
        else:
            invented.append(x_str)
    return invented, derived


def check_betz(answer, question, tool_calls):
    issues = []
    for tc in tool_calls:
        try:
            data = json.loads(tc["output"])
        except ValueError:
            continue
        if not isinstance(data, dict):
            continue
        cps = []
        for key in ("cp", "best_cp", "cp_baseline", "cp_best_found"):
            value = data.get(key)
            if value is not None:
                cps += value if isinstance(value, list) else [value]
        if any(c > BETZ for c in cps):
            issues.append(f"{tc['tool']} returned a Cp above the Betz limit {BETZ:.4f}")

    question_numbers = {float(n) for n in NUM_RE.findall(question)}
    for m in re.finditer(r"\bCp\b[^\n]{0,40}?\b(0\.\d+)", answer):
        value = float(m.group(1))
        if value > BETZ and value not in question_numbers and value not in BETZ_FACTS:
            issues.append(f"answer states a Cp of {value}, above the Betz limit")
    return issues


NEGATION = re.compile(r"\b(whether|not|cannot|can't|isn't|if|unless|no way)\b|n't\b", re.IGNORECASE)


def check_prose_rules(answer):
    sentences = re.split(r"(?<=[.!?])\s+|\n+", answer)
    hits = []
    for name, pattern in PROSE_RULES:
        for sentence in sentences:
            if re.search(pattern, sentence, re.IGNORECASE) and not NEGATION.search(sentence):
                hits.append(name)
                break
    return hits


def judge_prose(question, answer, tool_calls):
    evidence = "\n".join(
        f"{tc['tool']}({json.dumps(tc['input'])}) -> {tc['output']}" for tc in tool_calls
    ) or "(no tools were called)"
    prompt = f"""You check an AI assistant's answer about a wind turbine blade solver.

Facts about the tools: blade twist decreases from root to tip. "Best" means the highest Cp among the swept values only, not a true optimum. The tools accept only: tip-speed ratio 4 to 10, design angle of attack 2 to 12 degrees, 1 to 4 blades, chord cap 1.0 to 3.0 m. Saying a request is outside those ranges is correct and supported. Tools only report what is in the evidence below.

QUESTION:
{question}

EVIDENCE (tool outputs):
{evidence}

ANSWER:
{answer}

List every claim in the ANSWER that is a specific fact about the blade, the physics or the results and is NOT directly supported by the evidence. Examples: invented percentages of the span, claims about twist trends, calling a design "optimized", explanations of why a trend happens that the tools did not report, claims that contradict the evidence.
Do NOT flag: general definitions, the Betz limit (16/27, about 0.593), restated tool numbers, simple correct arithmetic on tool numbers, or the assistant asking the user for information.

Reply with JSON only: {{"unsupported": ["short claim", ...]}}. Use an empty list if there are none."""
    client = anthropic.Anthropic()
    response = client.messages.create(
        model=JUDGE_MODEL,
        max_tokens=3000,
        messages=[{"role": "user", "content": prompt}],
    )
    JUDGE_USAGE["in"] += response.usage.input_tokens
    JUDGE_USAGE["out"] += response.usage.output_tokens
    text = "".join(b.text for b in response.content if b.type == "text")
    match = re.search(r"\{.*\}", text, re.DOTALL)
    return json.loads(match.group(0))["unsupported"]


def check_answer(question, answer, tool_calls, tools_any=None, use_judge=True):
    fail, warn = [], []

    if answer is None:
        return {"status": "FAIL", "fail": ["hit the step limit with no final answer"], "warn": []}

    if tools_any is not None and not {tc["tool"] for tc in tool_calls} & set(tools_any):
        fail.append(f"expected one of {tools_any}, called {[tc['tool'] for tc in tool_calls] or 'nothing'}")

    invented, derived = check_numbers(answer, question, tool_calls)
    if invented:
        fail.append(f"numbers not in any tool result: {invented}")
    if derived:
        warn.append(f"numbers the model calculated itself (arithmetic matches tool numbers): {derived}")

    fail.extend(check_betz(answer, question, tool_calls))

    for rule in check_prose_rules(answer):
        fail.append(f"prose rule: {rule}")

    if use_judge:
        try:
            for claim in judge_prose(question, answer, tool_calls):
                fail.append(f"judge: {claim}")
        except Exception as e:
            warn.append(f"judge did not run: {e}")

    return {"status": "FAIL" if fail else ("WARN" if warn else "PASS"), "fail": fail, "warn": warn}
