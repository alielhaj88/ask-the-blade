# "Ask the blade" agent v1: architecture recap

## What it is
A question goes in. The model (Claude Haiku 4.5) picks a solver function as a tool, the solver runs, and the model reports the tool's numbers. The model never does physics.

```
user question
   |
   v
ask_agent()  --->  Claude (system prompt + 5 tool schemas)
   ^                   |  stop_reason == "tool_use"?
   |                   v
   |            TOOLS[name](input)  --->  run_bem / run_sweep  --->  bem_core.py
   |                   |
   |        result or error string (trimmed, logged, repeat-guarded)
   |                   |
   +---- tool_result --+            (max 5 steps)
   |
   v
final answer  --->  check_answer.py (numbers, Betz, prose rules, judge)
```

## Files
| File | Job |
|---|---|
| `bem_core.py` | The solver. Importable, prints nothing. |
| `BEM_solver_agent.py` | Tool schemas, `RANGES`, `run_bem`, sweeps, `TOOLS`, `SYSTEM_PROMPT`, `ask_agent` loop, `tool_log.jsonl` logging |
| `test_questions.py` | 25 questions, each with the tools it should call |
| `check_answer.py` | Checks one answer against the tool outputs |
| `run_tests.py` | Runs the questions, prints PASS/WARN/FAIL, tracks cost, stops at a budget |
| `break_it.py` | The four break-on-purpose experiments (cheap, $0.03) |

## The pieces and why each exists
1. **Tool schema** (`design_blade_tool`): the model only knows a tool by its name, description and parameter list. `make_sweep_tool` builds each sweep schema from it, minus the swept key, because the sweep sets that value itself.
2. **Validation** (`RANGES`, `run_bem`): out-of-range input raises `ValueError` with a clear message. The model reads that message.
3. **Loop** (`ask_agent`): call the model, run any tools it asked for, send results back as `tool_result` blocks with `tool_use_id`, repeat. Stops when `stop_reason` is not `tool_use`, or after 5 steps.
4. **Error path**: `ValueError` sends its own message back. Any other exception sends `Tool failed with <type>: <message>`. Nothing crashes the loop.
5. **Guards**: results over 4,000 characters are trimmed. The same call a third time gets an error telling the model to answer.
6. **System prompt**: numbers only from tool results or the question, no own arithmetic, "best" means best of the values tested, no why-explanations, no outside facts except Betz, ask for missing inputs, don't call tools for out-of-range requests.
7. **`cp_spread`**: the sweep tool computes max Cp minus min Cp, so "which matters more" is answered with a tool number.
8. **Log**: every tool call is appended to `tool_log.jsonl`.
9. **Checker**: numbers (against question, tool inputs, tool outputs, allow-list of Betz and tool ranges), derived numbers (WARN), Betz, tool choice, prose rules, and a judge model for unsupported claims.

## What broke and what fixed it
| Problem | Fix |
|---|---|
| Invented numbers and percentages | System prompt rule, plus the checker |
| "optimal", "optimized", cost and weight explanations | Prompt bans them, prose rules catch them |
| Own arithmetic on "which matters more" | `cp_spread` from the tool |
| Unexpected exception crashed the loop | `except Exception` |
| Huge result would multiply cost | `trim_result` |
| Tool called forever | Step cap plus repeat guard |
| Vague descriptions made the model pick the wrong tool | Descriptions say what is varied and when to use the tool |

## Results
- Before the system prompt: 7 pass, 2 warn, 16 fail (25 questions).
- After: 20 of 25 re-run, all pass the number and rule checks. Q7 still gets one judge complaint about a loose phrase.
- Not re-run on the new prompt: Q11, Q14, Q18, Q24, Q25.

## Rebuild checklist (from an empty folder)
1. `bem_core.py` works and prints nothing on import.
2. Write one tool schema with units in the parameter names and a range in every description.
3. Write `RANGES` and a runner that raises `ValueError` on bad input and returns plain floats and lists (no numpy).
4. Write the loop: `tool_use` check, `block.id` into `tool_use_id`, `is_error`, step cap, `except Exception`.
5. Put tools in a `TOOLS` name-to-function dict.
6. Add the sweeps with one shared helper and one schema builder.
7. Add logging (append mode, one JSON per line).
8. Write the system prompt from the failures you saw, not from guesses.
9. Add trim and the repeat guard.
10. Build the test questions, then the checker. Run with `--no-judge` first, and watch the cost line.
11. Break it on purpose after it works: vague description, throwing tool, huge result, no stop.

## Web interface (V1)
- `python3 app.py` runs the live agent at http://localhost:8000 with a $0.10 spending cap per server session (`--budget 0.25` changes it). `python3 app.py --mock` makes no API calls: it picks a tool from keywords and runs the real solver.
- `app.py` uses only Python's standard library. `POST /api/chat` streams events (`tool_start`, `tool_result`, `usage`, `answer`, `error`, `done`) as the agent works; `GET /api/status` gives mode and spend.
- `ask_agent(question, history=None, on_event=None)`: `history` carries earlier text turns (last 6 pairs) so follow-ups work; `on_event` is how the server streams each step. Called with one argument it behaves as before.
- `web/` is plain HTML/CSS/JS. Chats are saved in the browser only. `/?ask=...` opens a chat and sends that question; `/?theme=light` forces the light theme.
- To host it later: same frontend, a hosted Python server, the key as a server environment variable, plus rate limiting per visitor.

## Known limits
- `sweep_tsr` gives each blade its own design speed, so TSR 5.5 and 7 look equal. This is not Phase 7.1's window peak.
- 3 of 16 corner combinations of the ranges do not converge (4 blades, 3.0 m cap).
- Small whole numbers in an answer (1 to 4) match the tool numbers loosely, so the number check is weakest on them.
- The judge costs about $0.012 to $0.02 per question. Use it on a few questions only.
- `cp_spread` compares different swept ranges (2 to 12 degrees against 1.0 to 3.0 m). The answer says it covers the values tested.
