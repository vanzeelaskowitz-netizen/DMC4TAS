"""Camera-space geometry. Screen coordinates are fractions of the client area."""
from math import atan2, degrees


def view_point(point, matrix):
    return [sum(v * matrix[i * 4 + k] for i, v in enumerate(point)) + matrix[12 + k]
            for k in range(3)]


def project(point, view, projection):
    if point is None:
        return None
    x, y, z = view_point(point, view)
    if z > -1:
        return None
    return {"x": .5 - x * projection[0] / (2 * z), "y": .5 + y * projection[5] / (2 * z), "depth": -z}


def camera_frame(combat, view, projection):
    player = (combat["player"] or {}).get("position")
    enemies = combat["enemies"] or []
    target = min((e for e in enemies if e["distance"] is not None), key=lambda e: e["distance"], default=None)
    angle = None
    if player and target:
        px, _, pz = view_point(player, view)
        ex, _, ez = view_point(target["position"], view)
        angle = degrees(atan2(ez - pz, px - ex))
    return {"angle": angle, "target": target["pointer"] if target else None,
            "player": project(player, view, projection),
            "enemies": {e["pointer"]: project(e["position"], view, projection) for e in enemies}}
