# Airfoil data sources

Polars (`*_polar.csv`): XFOIL results downloaded from airfoiltools.com on 12 Oct 2026.
Reynolds number 1,000,000, Ncrit 9, Mach 0. These are computed (XFOIL), not wind-tunnel data.

| Name | Airfoiltools polar key | Outline (`*.dat`, Selig format) |
|---|---|---|
| naca0012 | xf-n0012-il-1000000 | n0012-il |
| naca4412 | xf-naca4412-il-1000000 | naca4412-il |
| naca4415 | xf-naca4415-il-1000000 | naca4415-il |
| s809 | xf-s809-nr-1000000 | s809-nr (NREL wind turbine airfoil) |

Limits:
- A 20 m blade at these speeds runs at Reynolds numbers of a few million, higher than 1,000,000.
- Outside the tabulated angle range, `bem_core.airfoil` extends the data with the Viterna method. That part is a model, not data.
- `standin` is the original made-up linear polar and is not real data.
