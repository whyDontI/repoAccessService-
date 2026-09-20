"""Pure graph-building for the team hierarchy fixture. No DB access here on
purpose, so this is easy to read and reason about on its own.
generate_fixture.py calls these and handles the actual database writes.
"""


def build_team_spine(team_ids, depth):
    """First `depth` team ids become a straight chain, root to leaf.

    This guarantees one concrete chain that actually reaches max depth,
    instead of hoping random attachment happens to get there.
    """
    spine = team_ids[:depth]
    edges = [(spine[i], spine[i - 1]) for i in range(1, len(spine))]
    depth_by_team = {team_id: i for i, team_id in enumerate(spine)}
    return edges, depth_by_team


def attach_remaining_teams(rng, team_ids, edges, depth_by_team, max_depth):
    """Every team not already placed gets attached under a random team
    that isn't already at max depth."""
    placed = set(depth_by_team)
    for team_id in team_ids:
        if team_id in placed:
            continue
        eligible = [t for t, d in depth_by_team.items() if d < max_depth - 1]
        parent_id = rng.choice(eligible)
        depth_by_team[team_id] = depth_by_team[parent_id] + 1
        edges.append((team_id, parent_id))
        placed.add(team_id)
    return edges, depth_by_team


def add_second_parents(rng, team_ids, edges, fraction):
    """A handful of teams get a second, independent parent.

    The schema doesn't guarantee one parent per team, so the fixture
    should actually exercise that instead of only ever producing a clean
    tree that hides the case.
    """
    sample_size = int(len(team_ids) * fraction)
    for team_id in rng.sample(team_ids, sample_size):
        other_parent = rng.choice(team_ids)
        if other_parent != team_id:
            edges.append((team_id, other_parent))
    return edges


def plant_cycles(rng, edges, count, exclude=frozenset()):
    """Makes a handful of teams cyclic: walks down a few hops from a
    random team and adds an edge back up, so a descendant ends up as its
    own ancestor's parent too.

    R4 expects real cycles to be in the data. Since we write the
    generator ourselves, that only happens if we put them there.
    `exclude` keeps chosen teams (e.g. a spine built for a clean,
    known-depth benchmark case) out of the picture, since making one of
    them cyclic too would muddy that case.
    Returns the new edges plus the (ancestor, descendant) pairs made
    cyclic, so the manifest can record exactly which teams they are.
    """
    children_of = {}
    for child, parent in edges:
        children_of.setdefault(parent, []).append(child)

    planted = []
    candidates = [
        team for team, kids in children_of.items() if kids and team not in exclude
    ]
    for ancestor in rng.sample(candidates, min(count, len(candidates))):
        descendant = ancestor
        for _ in range(rng.randint(2, 5)):
            kids = children_of.get(descendant)
            if not kids:
                break
            descendant = rng.choice(kids)
        if descendant != ancestor:
            edges.append((ancestor, descendant))
            planted.append((ancestor, descendant))
    return edges, planted
