# "Ask the blade": architecture recap (SHIP 2)

## What it is
A question goes in. The model (Claude Haiku 4.5) picks a solver function as a tool, the solver runs, and the model reports the tool's numbers. The model never does physics.

```
user question
   |
   v
ask_agent()  --->  Claude (system prompt + 11 tool schemas)
   ^                   |  stop_reason == "tool_use"?
   |                   v
   |            TOOLS[name](input)  --->  run_bem / run_sweep / optimisers / STL  --->  bem_core.py
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
| `chord_opt.py` | Phase 8: `optimize_chord` (best chord per ring, numpy only) and `plot_planform` |
| `twist_opt.py` | Phase 9: `optimize_twist` (best twist offset per ring) and `plot_twist` |
| `data/airfoils/` | Phase 10: XFOIL polars and outlines for NACA 0012, 4412, 4415 and S809 (see `SOURCES.md`) |
| `design_opt.py` | Phase 11: `optimize_blade` (chord + twist per ring) and `optimize_design` (grid over design inputs with thrust/area limits) |
| `blade_geometry.py` | Phase 11: 3D mesh from chord + twist + airfoil outline, watertight check, STL writer |
| `evals.py` | 109 automatic evals, no API calls (regression numbers, physics, optimisers, geometry, guards) |
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

## Phase 8: chord optimisation
- `optimize_chord` in `chord_opt.py` is the sixth tool. Each ring's power depends only on its own chord, so it tries chords from 0.1 m up to the cap in 0.05 m steps on all rings at once and keeps the winner per ring. Twist stays at the baseline twist.
- Result for lambda 7, alpha 6, 3 blades, cap 2.0 m: Cp 0.4978 to 0.499, thrust 42,531 N to 41,588 N, 3 rings at the cap. The baseline blade was already close to the best this stand-in polar allows.
- The result carries `baseline_all_rings_converged`. If it is false (e.g. 4 blades, 3.0 m cap) the baseline Cp is not a fair comparison.
- Not yet run through the live agent or the 25-question test set (API cost). The prompt allows "best found", not "optimal" or "optimized".
- `plot_planform(result)` saves a baseline vs best-found outline to `chord_planform.png`.

## Phase 9: twist optimisation
- `optimize_twist` keeps the baseline chord and tries the same twist offset (-15 to +8 degrees, 0.25 degree steps) on every ring, keeping the best offset per ring.
- Result for lambda 7, alpha 6, 3 blades, cap 2.0 m (stand-in): Cp 0.4978 to 0.502. The root ring gains most (offset -8.5 degrees). It reports `rings_at_search_limit` so a result at the edge of the range is visible.

## Phase 10: real airfoils
- `bem_core.airfoil(alpha, polar)` reads XFOIL polars (Re 1,000,000, airfoiltools.com) and interpolates. Outside the table it uses the Viterna extension. `design_blade`, `solve_ring` and `solve_rotor` take `polar` as a last argument that defaults to `"standin"`, so every earlier result is unchanged.
- `compare_airfoils` (lambda 7, alpha 6, 3 blades, cap 2.0 m): NACA 0012 0.5108, NACA 4412 0.5415, NACA 4415 0.5385, S809 0.5267. Stand-in 0.4978.
- Every tool takes an optional `airfoil` input. Without it, tools use the stand-in, so the old test answers still hold.

## Phase 11: multi-variable optimisation, 3D, STL, evals
- `optimize_blade`: chord and twist together, every (chord, offset) pair on all rings at once, best pair per ring. About 0.7 s.
- `optimize_design`: 4,200 designs (7 tip-speed ratios x 6 angles x 4 blade counts x 5 caps x 5 airfoils, fewer if the blade count or airfoil is fixed), keeps the ones that converge and meet `max_thrust_N` / `max_blade_area_m2`, then runs `optimize_blade` on the top 3 and keeps a refinement only if it still meets the limits. About 15 s.
- `export_blade_stl`: builds the blade (sections at the hub, the 10 ring midpoints and the tip, pitch axis at 25% chord, turned by the twist), checks it is watertight, writes `exports/*.stl` in metres.
- Web: `/api/mesh` and `/api/stl` take a blade shape and return a mesh or an STL; `/exports/<file>.stl` serves saved files. The page shows a 3D viewer (three.js r128 in `web/vendor/`) and an STL download on every card that has a blade shape.
- `evals.py`: 109 checks with no API cost. Run it after every change.

## Web interface (V1)
- `python3 app.py` runs the live agent at http://localhost:8000 with a $0.10 spending cap per server session (`--budget 0.25` changes it). `python3 app.py --mock` makes no API calls: it picks a tool from keywords and runs the real solver.
- `app.py` uses only Python's standard library. `POST /api/chat` streams events (`tool_start`, `tool_result`, `usage`, `answer`, `error`, `done`) as the agent works; `GET /api/status` gives mode and spend.
- `ask_agent(question, history=None, on_event=None)`: `history` carries earlier text turns (last 6 pairs) so follow-ups work; `on_event` is how the server streams each step. Called with one argument it behaves as before.
- `web/` is plain HTML/CSS/JS. Chats are saved in the browser only. `/?ask=...` opens a chat and sends that question; `/?theme=light` forces the light theme.
- To host it later: same frontend, a hosted Python server, the key as a server environment variable, plus rate limiting per visitor.

## Known limits
- No tip-loss or hub-loss correction. Real-airfoil Cp values (0.51 to 0.54) are higher than a real rotor would reach.
- XFOIL polars at Re 1,000,000; the real blade runs at a few million. Viterna extension past the table is a model, not data.
- The searches are grids, ring by ring: "best found", not a proven optimum. Neighbouring rings can jump in chord or twist.
- The 3D blade reuses the nearest ring's chord and twist at the hub and tip, and draws the stand-in polar with a NACA 4412 outline.
- With 11 tools the agent costs about $0.014 per question (was about $0.008).
- `sweep_tsr` gives each blade its own design speed, so TSR 5.5 and 7 look equal. This is not Phase 7.1's window peak.
- 3 of 16 corner combinations of the ranges do not converge (4 blades, 3.0 m cap).
- Small whole numbers in an answer (1 to 4) match the tool numbers loosely, so the number check is weakest on them.
- The judge costs about $0.012 to $0.02 per question. Use it on a few questions only.
- `cp_spread` compares different swept ranges (2 to 12 degrees against 1.0 to 3.0 m). The answer says it covers the values tested.
