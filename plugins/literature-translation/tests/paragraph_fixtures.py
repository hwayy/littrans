"""Test-only migration of known synthetic label evidence in hand-built layout plans."""
from __future__ import annotations

from littrans.source_structure import assemble_structure


def assemble_fixture_structure(units, assets, plan, make_unit, **kwargs):
    """Hand-built fixtures bypass the glyph planner, so provide its proposed labels."""
    labels = dict(plan.get("list_items", {}))
    for unit in units:
        token = unit.source_text.lstrip("* ").split(" ", 1)[0].strip("*")
        # These fixtures explicitly print bracketed clauses/closed exercise numbers.
        if ((token.startswith("(") and token.endswith(")"))
                or (token.endswith((".", ")")) and any(c.isdigit() for c in token))
                or (len(token) == 2 and token[1] == ")")):
            labels.setdefault(unit.unit_id.split("-", 1)[1], {"label": token})
    return assemble_structure(units, assets, {**plan, "list_items": labels}, make_unit, **kwargs)
