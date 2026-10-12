"""
blade_geometry.py -- Phase 11: turn chord and twist into a 3D blade (mesh + STL).

Coordinates (metres): x = chordwise in the rotor plane, y = radius (along the
blade), z = along the wind. Each section is the airfoil outline scaled by the
chord, placed with its pitch axis at 25% chord, and turned by the twist angle.
"""
import os

import numpy as np
from bem_core import DATA_DIR, R, r_hub

PITCH_AXIS = 0.25          # fraction of chord from the leading edge
SHAPE_FOR = {"standin": "naca4412"}   # the stand-in polar has no real shape; draw it as a NACA 4412


def load_outline(airfoil):
    """Airfoil outline in Selig order (trailing edge -> upper -> leading edge -> lower), chord 1."""
    name = SHAPE_FOR.get(airfoil, airfoil)
    pts = []
    for line in open(os.path.join(DATA_DIR, f"{name}.dat")):
        parts = line.split()
        try:
            pts.append((float(parts[0]), float(parts[1])))
        except (ValueError, IndexError):
            continue                                  # header line
    pts = np.array(pts)
    if np.allclose(pts[0], pts[-1]):
        pts = pts[:-1]                                # drop a repeated closing point
    return pts


def build_mesh(radius_m, chord_m, twist_deg, airfoil="standin"):
    """Return (vertices, faces): vertices is (n, 3) floats, faces is (m, 3) vertex indices."""
    radius_m, chord_m, twist_deg = map(np.asarray, (radius_m, chord_m, twist_deg))
    # Stations: hub edge, the ring midpoints, the tip. Ends reuse the nearest ring's chord and twist.
    rs = np.concatenate([[r_hub], radius_m, [R]])
    cs = np.concatenate([[chord_m[0]], chord_m, [chord_m[-1]]])
    ts = np.radians(np.concatenate([[twist_deg[0]], twist_deg, [twist_deg[-1]]]))

    outline = load_outline(airfoil)
    n = len(outline)
    sections = []
    for y, c, t in zip(rs, cs, ts):
        xs = (outline[:, 0] - PITCH_AXIS) * c         # chordwise, pitch axis at 0
        zs = outline[:, 1] * c                        # thickness direction
        x = xs * np.cos(t) - zs * np.sin(t)           # turn by the twist angle
        z = xs * np.sin(t) + zs * np.cos(t)
        sections.append(np.column_stack([x, np.full(n, y), z]))
    verts = np.vstack(sections)

    faces = []
    for s in range(len(rs) - 1):                      # skin: two triangles between neighbouring sections
        a, b = s * n, (s + 1) * n
        for i in range(n):
            j = (i + 1) % n                           # wraps round, which also closes the trailing edge
            faces += [(a + i, a + j, b + j), (a + i, b + j, b + i)]

    # caps: a fan from the centre of the root and tip sections
    root_c = len(verts)
    tip_c = root_c + 1
    last = (len(rs) - 1) * n
    verts = np.vstack([verts, sections[0].mean(axis=0), sections[-1].mean(axis=0)])
    for i in range(n):
        j = (i + 1) % n
        faces.append((root_c, j, i))
        faces.append((tip_c, last + i, last + j))
    faces = np.array(faces)

    if signed_volume(verts, faces) < 0:               # make normals point outwards
        faces = faces[:, ::-1]
    return verts, faces


def signed_volume(verts, faces):
    v0, v1, v2 = verts[faces[:, 0]], verts[faces[:, 1]], verts[faces[:, 2]]
    return float(np.sum(np.einsum("ij,ij->i", v0, np.cross(v1, v2))) / 6)


def is_watertight(faces):
    """Every edge used exactly twice, once in each direction."""
    edges = {}
    for f in faces:
        for a, b in ((f[0], f[1]), (f[1], f[2]), (f[2], f[0])):
            edges[(a, b)] = edges.get((a, b), 0) + 1
    return all(cnt == 1 and edges.get((b, a)) == 1 for (a, b), cnt in edges.items())


def stl_text(verts, faces, name="blade"):
    """ASCII STL as one string, units metres."""
    v0, v1, v2 = verts[faces[:, 0]], verts[faces[:, 1]], verts[faces[:, 2]]
    normals = np.cross(v1 - v0, v2 - v0)
    lengths = np.linalg.norm(normals, axis=1, keepdims=True)
    normals = np.divide(normals, lengths, out=np.zeros_like(normals), where=lengths > 0)
    lines = [f"solid {name}"]
    for nrm, a, b, c in zip(normals, v0, v1, v2):
        lines.append(f"  facet normal {nrm[0]:.6e} {nrm[1]:.6e} {nrm[2]:.6e}")
        lines.append("    outer loop")
        for p in (a, b, c):
            lines.append(f"      vertex {p[0]:.6e} {p[1]:.6e} {p[2]:.6e}")
        lines.append("    endloop")
        lines.append("  endfacet")
    lines.append(f"endsolid {name}")
    return "\n".join(lines) + "\n"


def write_stl(path, verts, faces, name="blade"):
    with open(path, "w") as f:
        f.write(stl_text(verts, faces, name))
    return os.path.abspath(path)


if __name__ == "__main__":
    from bem_core import design_blade, r
    c, beta = design_blade(7 * 8 / 20, 6, 1/3, 2.0, 3)
    v, f = build_mesh(r, c, np.degrees(beta), "standin")
    print(len(v), "vertices,", len(f), "triangles, watertight:", is_watertight(f),
          "volume m3:", round(signed_volume(v, f), 3))
    print("saved", write_stl("baseline_blade.stl", v, f))
