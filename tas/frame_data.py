"""Derive public frame fields from samples taken at the two ends of one logic tick."""
from camera import camera_frame


def hits(combat, before):
    player, enemies = combat["player"], combat["enemies"] or []
    events = []
    for actor in [player, *enemies]:
        if not actor or actor["hp"] is None:
            continue
        hp = before.get(actor["pointer"])
        if hp is None or hp <= actor["hp"]:
            continue
        # HP has no attacker pointer. Only attribute a blow in the one-opponent scenario.
        source = None
        if player and combat["enemiesComplete"] and len(enemies) == 1:
            source = (enemies[0] if actor is player else player)["pointer"]
        events.append({"target": actor["pointer"], "source": source, "damage": hp - actor["hp"],
                       "attribution": "inferred" if source else "unknown"})
    return events


def enrich(row):
    combat = (row or {}).get("combat")
    if row and combat is None:
        row.update(camera=None, hits=None)
    elif combat and "matrices" in combat:
        matrices = combat.pop("matrices")
        row["camera"] = camera_frame(combat, **matrices) if matrices else None
        row["hits"] = hits(combat, combat.pop("hpBefore"))
