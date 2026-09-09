# Approach plan

<!-- lokay-approach source=deterministic repo=mikolaj92/anonimizator3000 issue=45 -->

Repository: `mikolaj92/anonimizator3000`  
Issue: #45 — Pin BOM v0.6.12 / v0.4.6 / v0.5.8 (jedna generacja)

## Goal

Host musi pinować **jedną niemienną generację** z `COMPAT.md` (tagi, nie `branch=main` i nie `path`). Issue opisywał mieszankę v0.6.11 / v0.4.6 / v0.5.8 i chciał wiersz v0.6.12 / v0.4.6 / v0.5.8. Na tym worktree `main` już przeszedł dalej: aktualny wiersz to **v0.6.22 / v0.5.4 / v0.6.5**. Nie cofamy nowszej spójnej generacji.

## Files likely touched

- `pyproject.toml`
- `tests/test_dependency_pins.py`
- `README.md`
- `uv.lock`

## Test plan

- `tool.uv.sources` pinuje jedną generację: app-factory **v0.6.22** (tag, nie `branch=main` i nie `path`), my-auth **v0.5.4**, my-usermanager **v0.6.5**.
- `override-dependencies` zostaje tylko `app-factory[platform]`.
- `uv.lock` zgadza się z tagami.
- README i `tests/test_dependency_pins.py` opisują aktualny wiersz COMPAT; znikają asercje „COMPAT.md has no v0.6.11 line” / „latest v0.6.10”.
- `uv run pytest tests/test_dependency_pins.py` przechodzi.

## Non-goals

- Migracja forków `install_passkey_ui` (osobny ticket). Posejdon / Docxtor / Fala carrier.

## Notes

- Trust intentional issue; this plan is evidence for later review, not a human gate.
- Coding agent may refine details but should stay on the stated goal and non-goals.
- Collector boundary: if implementation introduces unbounded collection, ship only a bounded collector patch that starts durably in the background after merge. The coding agent and lokay must not populate data or wait for collection to finish.
