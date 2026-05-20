---
sidebar_position: 8
title: Responsive Layout
---

# Responsive Layout

The dashboard frontend supports desktop, tablet, and mobile (including iPhones with a notch / dynamic island) from a **single React/CSS codebase**. There is no separate mobile app: the same routes, the same components, just different viewport-aware rendering.

This page documents the building blocks that keep desktop and mobile from diverging silently as new features land.

## Breakpoints

A single source of truth in `frontend/src/lib/breakpoints.ts`:

```ts
export const BP = {
  mobile: 768,
  tablet: 1024,
  desktop: 1280,
} as const;

export const mq = {
  mobile:     `(max-width: ${BP.mobile - 1}px)`,
  tabletUp:   `(min-width: ${BP.mobile}px)`,
  tabletOnly: `(min-width: ${BP.mobile}px) and (max-width: ${BP.tablet - 1}px)`,
  desktopUp:  `(min-width: ${BP.tablet}px)`,
  wideUp:     `(min-width: ${BP.desktop}px)`,
  reducedMotion: "(prefers-reduced-motion: reduce)",
} as const;
```

| Tier | Range | Reference device |
|------|-------|------------------|
| `mobile` | width < 768px | iPhone SE (375×667), iPhone 12 Pro (390×844) |
| `tablet` | 768px ≤ width < 1024px | iPad Mini portrait |
| `desktop` | width ≥ 1024px | Laptops, monitors |
| `wide` | width ≥ 1280px | Large external displays |

Always reference these constants from TS and the same values from CSS (via `var(--bp-*)` once we migrate the remaining literal `@media (max-width: NNNpx)` rules). Never hard-code pixel breakpoints in new code — the responsive lint blocks it (see below).

## `useBreakpoint()` — runtime detection

For React components that need to branch on viewport (e.g., show a hamburger on mobile, a sidebar on desktop), use the matchMedia-based hook:

```tsx
import { useBreakpoint } from "@/hooks/useBreakpoint";

function Navigation() {
  const { isMobile, isTablet, isDesktop, reducedMotion } = useBreakpoint();
  return isMobile ? <MobileNav /> : <DesktopNav />;
}
```

Properties:

- **SSR-safe** — falls back to a sensible default if `window.matchMedia` is unavailable.
- **Reactive** — subscribes to `matchMedia.addEventListener("change", ...)`, so the component re-renders when the user resizes the window or rotates the device.
- **Cheap** — does *not* trigger a layout read on every component re-render the way `window.innerWidth` does.

Direct reads of `window.innerWidth` / `window.innerHeight` are forbidden in production components — use this hook instead. The lint guard catches new offenders (see [Responsive lint](#responsive-lint)).

## iOS safe-area handling

iPhones with a notch or dynamic island (iPhone X and later) clip content behind the system UI unless the page opts into the full viewport. The dashboard does this in two places:

1. `frontend/index.html`:
   ```html
   <meta name="viewport"
         content="width=device-width, initial-scale=1.0, viewport-fit=cover" />
   ```
2. `frontend/src/index.css` adds `env(safe-area-inset-*)` padding on the chrome:
   ```css
   .app-header {
     padding: max(6px, env(safe-area-inset-top))
              max(20px, env(safe-area-inset-right))
              6px
              max(20px, env(safe-area-inset-left));
   }
   .chat-input {
     padding: 10px max(16px, env(safe-area-inset-right))
              max(10px, env(safe-area-inset-bottom))
              max(16px, env(safe-area-inset-left));
   }
   ```

The `max()` pattern keeps the desktop layout unchanged (the env() value is `0` on browsers that don't expose it) while reserving room for the notch, the home indicator, and the curved side bezels on iOS.

## Mobile-specific layout rules

Most desktop styles are reused as-is. Only the rules that *change* on mobile live inside `@media (max-width: 600px)` in `frontend/src/index.css`:

- **Touch targets ≥ 44 × 44 px** — buttons, segment controls, send icon, dropdown rows — per Apple HIG and WCAG 2.5.5.
- **Inputs ≥ 16px font-size** — prevents iOS Safari from auto-zooming when the user taps a textarea / `<select>`.
- **Hamburger replaces the desktop left rail** — the History/Prompts/Logs panels become a drawer overlay opened from the top-left ☰.
- **Mode/Provider segment radios collapse behind the ⚙ gear** — only the model picker, RAG checkbox, and stream toggle stay visible by default. The gear lives in the bottom action bar, next to **New Chat**.
- **Native `<select>` pickers are swapped for inline radio segments** — the native lists misaligned in several mobile browsers; the new buttons sit on the same line as the gear.
- **Header is compact and wraps gracefully** — metrics bar, cumulative tokens, and the cache widget are hidden; the title stays one line; logs / SSE toggles drop to a second row when the title is long.

## Responsive lint

`scripts/check_responsive.sh` is a CI guard wired into the `Test Suite` job in `.github/workflows/deploy.yml`. It scans `frontend/src/` for:

- `window.innerWidth` / `window.innerHeight` reads (`useBreakpoint()` should be used instead)
- `@media (max-width|min-width: NNNpx)` literals (the canonical values live in `breakpoints.ts`)
- `matchMedia("...NNNpx...")` calls outside the hook itself

A baseline file (`frontend/.responsive-baseline.txt`) pins the pre-existing violations so the check passes today and *blocks any new ones*. After an intentional migration, rebuild the baseline:

```bash
bash scripts/check_responsive.sh --update-baseline
```

CI fails with a clear pointer when a new violation slips in.

## Cross-viewport smoke E2E

`frontend/e2e/chat-smoke.spec.ts` runs once for each project in `playwright.config.ts`:

| Project | Viewport | Engine |
|---------|----------|--------|
| `desktop` | 1440 × 900 | Chromium |
| `mobile`  | 375 × 667 (iPhone SE) | Chromium |

It exercises the chat happy-path — send → loading indicator → click Stop → "Stopped by user." — with all `/api/*` and `/auth/*` routes mocked, so it doesn't depend on the backend or any LLM.

Run locally:

```bash
cd frontend
npm run e2e          # both viewports, headless
npm run e2e:ui       # interactive Playwright UI mode
```

In CI the suite runs on the `frontend-e2e` job; the HTML report is uploaded as an artifact on failure.

## PR checklist

Every UI-touching PR follows the `Responsive check` block in `.github/PULL_REQUEST_TEMPLATE.md`:

```markdown
- [ ] Tested at 375px width (iPhone SE)
- [ ] Tested at 1440px width (desktop)
- [ ] No new hardcoded breakpoints — uses `BP.*` from `frontend/src/lib/breakpoints.ts`
- [ ] No literal `window.innerWidth` reads — uses `useBreakpoint()` instead
- [ ] Animations honor `prefers-reduced-motion` where applicable
```

The lint script and the cross-viewport E2E catch most regressions automatically, but the box still has to be ticked — reviewers won't merge a UI PR without it.

## What's intentionally *not* in this stack

- **Visual regression testing** (`expect(page).toHaveScreenshot()`). Deferred until the dashboard has more stable screens; the flake-to-value ratio is poor with `<5` covered screens.
- **A device emulator beyond Chromium**. Adding WebKit / Firefox in CI doubles the suite time and rarely catches dashboard-specific regressions (we use Chromium-only locally too).
- **Separate mobile bundle**. The single React build serves all viewports. CSS does the heavy lifting; JS only branches via `useBreakpoint()` where it must.

## See also

- [`frontend/src/lib/breakpoints.ts`](https://github.com/pjcau/agent-orchestrator/blob/main/frontend/src/lib/breakpoints.ts)
- [`frontend/src/hooks/useBreakpoint.ts`](https://github.com/pjcau/agent-orchestrator/blob/main/frontend/src/hooks/useBreakpoint.ts)
- [`scripts/check_responsive.sh`](https://github.com/pjcau/agent-orchestrator/blob/main/scripts/check_responsive.sh)
- [`frontend/playwright.config.ts`](https://github.com/pjcau/agent-orchestrator/blob/main/frontend/playwright.config.ts)
- [Components](./components) — how each component plugs into the dashboard
