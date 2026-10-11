"""
bem_core.py -- the BEM solver as importable definitions only.
Nothing in this file prints, plots or runs a study on import.
"""
import numpy as np

# ==================== Turbine constants ====================
V_inf = 8.0       # m/s, wind speed
R = 20.0          # m, rotor radius
B = 3             # default number of blades
rho = 1.225       # kg/m^3

# ==================== Rotor discretization ====================
N = 10            # number of rings
r_hub = 2.0       # m

edges = np.linspace(r_hub, R, N + 1)
r = (edges[:-1] + edges[1:]) / 2      # ring midpoints
dr = (R - r_hub) / N                  # ring width

# ==================== Solver settings ====================
tol = 1e-6
max_iter = 100


def airfoil(alpha):
    a_deg = np.degrees(alpha)                                       # alpha in radians
    C_L = 0.11 * (a_deg + 2)
    C_L = np.where(a_deg > 12, 1.54 - 0.05 * (a_deg - 12), C_L)     # after stall
    C_D = 0.008 + 0.01 * C_L**2
    C_D = np.where(a_deg > 12, C_D + 0.02 * (a_deg - 12), C_D)      # after stall
    return C_L, C_D


def design_blade(omega_design, alpha_d_deg, a_target, c_max, B=3):
    alpha_d = np.radians(alpha_d_deg)
    phi_d = np.arctan(V_inf * (1 - a_target) / (omega_design * r))
    beta_d = phi_d - alpha_d

    C_L_d, C_D_d = airfoil(alpha_d)
    C_n_d = C_L_d * np.cos(phi_d) + C_D_d * np.sin(phi_d)
    sigma_d = 4 * np.sin(phi_d)**2 * a_target / ((1 - a_target) * C_n_d)
    c_d = np.minimum(sigma_d * 2 * np.pi * r / B, c_max)

    return c_d, beta_d


def solve_ring(r_i, c_i, beta_i, omega, B=3):
    sigma = (B * c_i) / (2 * np.pi * r_i)
    a = 0.3
    a_prime = 0.01

    converged = False

    for i in range(max_iter):
        V_axial = V_inf * (1 - a)
        V_tan = omega * r_i * (1 + a_prime)

        phi = np.arctan(V_axial / V_tan)
        alpha = phi - beta_i

        C_L, C_D = airfoil(alpha)
        C_n = C_L * np.cos(phi) + C_D * np.sin(phi)
        C_t = C_L * np.sin(phi) - C_D * np.cos(phi)

        a_new = 1 / (1 + (4 * np.sin(phi)**2) / (sigma * C_n))
        a_prime_new = 1 / (4 * np.sin(phi) * np.cos(phi) / (sigma * C_t) - 1)

        if abs(a_new - a) < tol and abs(a_prime_new - a_prime) < tol:
            converged = True
            break

        a, a_prime = a_new, a_prime_new

    if not converged:
        print(f"Warning: ring at r = {r_i:.2f} m did not converge in {max_iter} iterations.")

    return a, a_prime, phi, alpha, i + 1, converged


def solve_rotor(omega, c, beta, B):
    a_arr = np.zeros(N)
    a_prime_arr = np.zeros(N)
    phi_arr = np.zeros(N)
    alpha_arr = np.zeros(N)
    iter_arr = np.zeros(N, dtype=int)
    conv_arr = np.zeros(N, dtype=bool)

    for i in range(N):
        (a_arr[i], a_prime_arr[i], phi_arr[i], alpha_arr[i],
         iter_arr[i], conv_arr[i]) = solve_ring(r[i], c[i], beta[i], omega, B)

    V_axial_arr = V_inf * (1 - a_arr)
    V_tan_arr = omega * r * (1 + a_prime_arr)
    V_rel_arr = np.sqrt(V_axial_arr**2 + V_tan_arr**2)
    C_L, C_D = airfoil(alpha_arr)
    C_n_arr = C_L * np.cos(phi_arr) + C_D * np.sin(phi_arr)
    C_t_arr = C_L * np.sin(phi_arr) - C_D * np.cos(phi_arr)

    dF_n_arr = 0.5 * rho * V_rel_arr**2 * c * C_n_arr * dr
    dF_t_arr = 0.5 * rho * V_rel_arr**2 * c * C_t_arr * dr
    dT_arr = B * dF_n_arr
    dQ_arr = B * r * dF_t_arr

    T = np.sum(dT_arr)
    Q = np.sum(dQ_arr)
    P = Q * omega
    P_wind = 0.5 * rho * np.pi * R**2 * V_inf**3
    Cp = P / P_wind
    C_T = T / (0.5 * rho * np.pi * R**2 * V_inf**2)
    lmbda = omega * R / V_inf

    dT_mom_arr = 4 * np.pi * rho * V_inf**2 * a_arr * (1 - a_arr) * r * dr
    T_mom = np.sum(dT_mom_arr)

    return {
        "omega": omega, "lmbda": lmbda,
        "a": a_arr, "a_prime": a_prime_arr, "phi": phi_arr, "alpha": alpha_arr,
        "iterations": iter_arr, "converged": conv_arr,
        "V_rel": V_rel_arr, "dT": dT_arr, "dQ": dQ_arr,
        "T": T, "Q": Q, "P": P, "Cp": Cp, "C_T": C_T,
        "T_mom": T_mom,
    }


def validate_rotor(res):
    print("Validation:")

    if res['Cp'] < 16 / 27:
        print(f"  [PASS] Cp = {res['Cp']:.4f} is below the Betz limit (0.5926)")
    else:
        print(f"  [FAIL] Cp = {res['Cp']:.4f} exceeds the Betz limit")

    if np.all((res['a'] >= 0) & (res['a'] <= 0.5)):
        print(f"  [PASS] all a between 0 and 0.5 (max a = {res['a'].max():.4f})")
    else:
        print(f"  [FAIL] some a outside 0-0.5 (min a = {res['a'].min():.4f}, "
              f"max a = {res['a'].max():.4f})")

    if np.all(res['converged']):
        print(f"  [PASS] all {len(res['converged'])} rings converged")
    else:
        print(f"  [FAIL] {np.sum(~res['converged'])} ring(s) did not converge")

    pct_diff = abs(res['T'] - res['T_mom']) / res['T_mom'] * 100
    if pct_diff < 1.0:
        print(f"  [PASS] BEM and momentum thrust agree ({pct_diff:.4f}% difference)")
    else:
        print(f"  [FAIL] BEM and momentum thrust disagree ({pct_diff:.2f}% difference)")