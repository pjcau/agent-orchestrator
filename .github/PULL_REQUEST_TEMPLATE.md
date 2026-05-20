## Summary

<!-- 1-3 bullets: what changed and why -->

## Test plan

<!-- Bulleted checklist of how this was verified -->

## Responsive check (UI changes only — delete if not applicable)

- [ ] Tested at 375px width (iPhone SE)
- [ ] Tested at 1440px width (desktop)
- [ ] No new hardcoded breakpoints — uses `BP.*` from `frontend/src/lib/breakpoints.ts`
- [ ] No literal `window.innerWidth` reads — uses `useBreakpoint()` instead
- [ ] Animations honor `prefers-reduced-motion` where applicable

## Docs / roadmap (delete sections that don't apply)

- [ ] Added/updated tests in `tests/` (Python) or `frontend/src/test/` (React)
- [ ] Updated relevant `docs/<area>.md` page
- [ ] Updated `docs/abstractions.md` if a new abstraction was introduced
- [ ] Re-ran `scripts/generate_architecture_map.py` + `scripts/generate_feature_map.py` if features changed
- [ ] Synced roadmap files (`docs/roadmap.md`, `docs/unified-roadmap.md`, `docs/website/docs/roadmap/`)
