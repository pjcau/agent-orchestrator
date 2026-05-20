import { useEffect, useState } from "react";
import { BP, mq } from "@/lib/breakpoints";

export interface BreakpointState {
  isMobile: boolean;
  isTablet: boolean;
  isDesktop: boolean;
  /** True when the user has prefers-reduced-motion enabled. */
  reducedMotion: boolean;
}

/** SSR-safe initial state — assumes desktop until window is available. */
function initialState(): BreakpointState {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return { isMobile: false, isTablet: false, isDesktop: true, reducedMotion: false };
  }
  return {
    isMobile: window.matchMedia(mq.mobile).matches,
    isTablet: window.matchMedia(mq.tabletOnly).matches,
    isDesktop: window.matchMedia(mq.desktopUp).matches,
    reducedMotion: window.matchMedia(mq.reducedMotion).matches,
  };
}

/**
 * Reactive breakpoint hook. Reads matchMedia and updates on viewport changes.
 *
 * Prefer this over `window.innerWidth` — it doesn't trigger a layout read on
 * every component re-render, is SSR-safe, and uses the same breakpoints as
 * the CSS layer (single source of truth in lib/breakpoints.ts).
 *
 * @example
 * const { isMobile } = useBreakpoint();
 * return isMobile ? <MobileNav /> : <DesktopNav />;
 */
export function useBreakpoint(): BreakpointState {
  const [state, setState] = useState<BreakpointState>(initialState);

  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
      return;
    }

    const queries = {
      mobile: window.matchMedia(mq.mobile),
      tablet: window.matchMedia(mq.tabletOnly),
      desktop: window.matchMedia(mq.desktopUp),
      reducedMotion: window.matchMedia(mq.reducedMotion),
    };

    const update = () => {
      setState({
        isMobile: queries.mobile.matches,
        isTablet: queries.tablet.matches,
        isDesktop: queries.desktop.matches,
        reducedMotion: queries.reducedMotion.matches,
      });
    };

    // Sync once in case state diverged between SSR and hydration.
    update();

    for (const q of Object.values(queries)) {
      q.addEventListener("change", update);
    }

    return () => {
      for (const q of Object.values(queries)) {
        q.removeEventListener("change", update);
      }
    };
  }, []);

  return state;
}

// Re-export the constants so consumers can `import { BP } from "@/hooks/useBreakpoint"`
// without a second import — minor ergonomic touch.
export { BP };
