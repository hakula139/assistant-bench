import { useCallback, useSyncExternalStore } from 'react';

const CHANGE = 'bench:search-change';

function subscribe(onChange: () => void) {
  window.addEventListener('popstate', onChange);
  window.addEventListener(CHANGE, onChange);
  return () => {
    window.removeEventListener('popstate', onChange);
    window.removeEventListener(CHANGE, onChange);
  };
}

/**
 * Reads and writes one query parameter, so filters survive reloads and shared links.
 *
 * The static render sees no query string, so it shows `fallback` until hydration reads the URL.
 * Setting `fallback` removes the parameter. A null fallback distinguishes an absent parameter
 * from every string value, including the empty string.
 */
export function useSearchParam<T extends string | null>(name: string, fallback: T) {
  const value = useSyncExternalStore(
    subscribe,
    () => new URLSearchParams(window.location.search).get(name) ?? fallback,
    () => fallback,
  );

  const setValue = useCallback(
    (next: string | T) => {
      const url = new URL(window.location.href);
      if (next === null || next === fallback) {
        url.searchParams.delete(name);
      } else {
        url.searchParams.set(name, next);
      }

      history.replaceState(history.state, '', url);
      window.dispatchEvent(new Event(CHANGE));
    },
    [name, fallback],
  );

  return [value, setValue] as const;
}
