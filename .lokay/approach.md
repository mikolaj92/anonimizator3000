# Approach plan

<!-- lokay-approach source=deterministic repo=mikolaj92/anonimizator3000 issue=37 -->

Repository: `mikolaj92/anonimizator3000`  
Issue: #37 — README: pyproject pinuje branch=main; pinuje tagi/rev

## Goal

README: „pyproject.toml wskazuje branche main”.
Faktycznie: app-factory tag v0.6.5, my-auth v0.4.2, posejdon v0.1.0, fala-runtime/doctotext po rev.

## Files likely touched

- `README.md` — replace the false `branche main` claim with the actual tag/rev pins
- `tests/test_dependency_pins.py` — lock pyproject sources and README wording

## Test plan

- `uv run pytest tests/test_dependency_pins.py`

## Non-goals

- Do not retarget `pyproject.toml` sources or change lockfile pins.

## Notes

- Trust intentional issue; this plan is evidence for later review, not a human gate.
- Coding agent may refine details but should stay on the stated goal and non-goals.
- Collector boundary: if implementation introduces unbounded collection, ship only a bounded collector patch that starts durably in the background after merge. The coding agent and mill must not populate data or wait for collection to finish.
- No explicit file paths in issue; infer from repo inspection.
