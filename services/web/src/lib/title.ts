import { useEffect } from "react";

// The tab is a place too. Several rooms and several tickets open at once is
// the normal way to use this platform, and a row of tabs all reading "Agent
// Platform" makes that pile unreadable — as do browser history and bookmarks
// saved from it.

/** What every tab ends with, so a tab from here is recognisable as one. */
export const APP_TITLE = "Agent Platform";

/**
 * Name the tab after the page, most specific part first:
 * `useTitle("OPS-1", "Weather repeats…")` → `OPS-1 · Weather repeats… · Agent
 * Platform`.
 *
 * Parts that are not yet known are dropped rather than rendered as holes, so a
 * page can call this once, above its own loading branches, and the title
 * sharpens as the data lands. The previous title is put back when the page
 * unmounts: a route that does NOT name itself inherits the app's name rather
 * than the last page's.
 */
export function useTitle(...parts: (string | null | undefined | false)[]): void {
  const title = [...parts.filter((p): p is string => !!p), APP_TITLE].join(" · ");
  useEffect(() => {
    const previous = document.title;
    document.title = title;
    return () => { document.title = previous; };
  }, [title]);
}
