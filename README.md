# Ask the Blade

A prototype that lets you ask plain-English questions about a 20 m wind turbine rotor. A language model (Claude Haiku 4.5) picks a solver function as a tool, a blade element momentum (BEM) solver runs the calculation, and the model reports the numbers the solver returned. The model never does the physics itself.

**Status: V1 prototype.** Built as part of a computational engineering portfolio.

## Read this first

- The airfoil lift and drag curves are a **stand-in polar, not real airfoil data**. The Cp and thrust numbers show how the agent works. They are not valid for real blade design. Real airfoil data is planned for a later phase.
- The solver covers a limited range: tip-speed ratio 4 to 10, angle of attack 2 to 12 degrees, 1 to 4 blades, chord cap 1.0 to 3.0 m. Out-of-range requests are refused.
- A few combinations at the corners of those ranges (4 blades with a 3.0 m chord cap) do not converge. See `ARCHITECTURE.md` for the known limits.

## What is in it

| File | Job |
|---|---|
| `bem_core.py` | The BEM solver. Importable, prints nothing. |
| `BEM_solver_agent.py` | Tool schemas, input ranges, the tool-use loop, the system prompt |
| `app.py` + `web/` | Local web chat interface (standard library server, plain HTML/CSS/JS) |
| `check_answer.py`, `test_questions.py`, `run_tests.py` | A 25-question test set and a checker for numbers and unsupported claims |
| `break_it.py` | Experiments that break the agent on purpose |
| `ARCHITECTURE.md` | How it fits together, what broke, and what fixed it |

## Run it

```
pip install -r requirements.txt
```

**Without an API key (free).** Mock mode picks a tool by keyword and still runs the real solver:

```
python3 app.py --mock
```

**With the live agent.** Create a `.env` file next to `app.py` containing:

```
ANTHROPIC_API_KEY=your-key-here
```

Then:

```
python3 app.py
```

Open http://localhost:8000. Each question costs roughly $0.006 to $0.012. The server stops at a $0.10 spending cap per run; change it with `--budget 0.25`.

Never commit `.env`. It is listed in `.gitignore`.

## License

No license is attached yet. This repository is public for viewing and archiving. All rights reserved: please do not copy or reuse the code without permission.
