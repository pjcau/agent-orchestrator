/**
 * Single source of truth for responsive breakpoints.
 *
 * Used by:
 * - useBreakpoint() — runtime detection in components
 * - CSS variables in index.css — drive @media queries via var(--bp-*)
 * - scripts/check_responsive.sh — validates no other literal values land in code
 *
 * Add a new breakpoint here AND mirror it as a CSS variable
 * (--bp-<name>) so the script keeps catching stragglers.
 */

export const BP = {
  mobile: 768,
  tablet: 1024,
  desktop: 1280,
} as const;

export type BreakpointName = keyof typeof BP;

/** Pre-built media query strings — use with matchMedia or styled-components. */
export const mq = {
  /** width < 768px */
  mobile: `(max-width: ${BP.mobile - 1}px)`,
  /** width >= 768px */
  tabletUp: `(min-width: ${BP.mobile}px)`,
  /** 768px <= width < 1024px */
  tabletOnly: `(min-width: ${BP.mobile}px) and (max-width: ${BP.tablet - 1}px)`,
  /** width >= 1024px */
  desktopUp: `(min-width: ${BP.tablet}px)`,
  /** width >= 1280px */
  wideUp: `(min-width: ${BP.desktop}px)`,
  /** User prefers reduced motion */
  reducedMotion: "(prefers-reduced-motion: reduce)",
} as const;
