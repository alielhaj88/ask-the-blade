# Ask the Blade

A prototype that lets you ask plain-English questions about a 20 m wind turbine rotor. A language model (Claude Haiku 4.5) picks a solver function as a tool, a blade element momentum (BEM) solver runs the calculation, and the model reports the numbers the solver returned. The model never does the physics itself.

**Status: SHIP 2.** Built as part of a computational engineering portfolio. It can design a blade, sweep inputs, search for better chord and twist, compare real airfoils, search a whole design space under thrust and area limits, and export the blade as a 3D STL.

## Read this first

- By default the airfoil is a **stand-in polar, not real data**. Four real airfoils (NACA 0012, 4412, 4415, NREL S809) are available as XFOIL polars at Reynolds number 1,000,000 (`data/airfoils/SOURCES.md`).
- The solver has **no tip-loss correction**, so Cp values are optimistic. Use the numbers to compare designs, not to predict a real turbine.
- The solver covers a limited range: tip-speed ratio 4 to 10, angle of attack 2 to 12 degrees, 1 to 4 blades, chord cap 1.0 to 3.0 m. Out-of-range requests are refused.
- A few combinations at the corners of those ranges (4 blades with a 3.0 m chord cap) do not converge. See `ARCHITECTURE.md` for the known limits.

## What is in it

| File | Job |
|---|---|
| `bem_core.py` | The BEM solver. Importable, prints nothing. |
| `BEM_solver_agent.py` | 11 tool schemas, input checks, the tool-use loop, the system prompt |
| `chord_opt.py`, `twist_opt.py`, `design_opt.py` | Searches for better chord, twist, both, and whole designs |
| `blade_geometry.py` | 3D blade mesh and STL export |
| `data/airfoils/` | Real airfoil polars and outlines, with sources |
| `evals.py` | 109 automatic checks, no API calls |
| `app.py` + `web/` | Local web chat interface (standard library server, plain HTML/CSS/JS) |
| `check_answer.py`, `test_questions.py`, `run_tests.py` | A 35-question test set and a checker for numbers and unsupported claims |
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

Open http://localhost:8000. Cards with a blade shape have **View in 3D** and **Download STL**.

**Check everything still works (free):**

```
python3 evals.py
``` Each question costs roughly $0.01 to $0.02. The server stops at a $0.10 spending cap per run; change it with `--budget 0.25`.

Never commit `.env`. It is listed in `.gitignore`.

## Third-party material

- Airfoil polars and outlines: airfoiltools.com (XFOIL results), see `data/airfoils/SOURCES.md`.
- `web/vendor/three.min.js`: three.js r128, MIT license.

## License

No license is attached yet. This repository is public for viewing and archiving. All rights reserved: please do not copy or reuse the code without permission.
