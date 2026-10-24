// @vitest-environment jsdom
import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, test } from 'vitest';

import { useSearchParam } from '@/lib/use-search-param';

describe('useSearchParam', () => {
  beforeEach(() => {
    history.replaceState(null, '', '/arena/?round=2026-10-21');
  });

  afterEach(cleanup);

  test('writes a chosen value to the URL and removes it again for the fallback', () => {
    const { result } = renderHook(() => useSearchParam('category', 'all'));
    const entries = history.length;

    act(() => {
      result.current[1]('docs');
    });
    const chosen = [result.current[0], window.location.search];
    act(() => {
      result.current[1]('all');
    });

    expect(chosen).toEqual(['docs', '?round=2026-10-21&category=docs']);
    expect([result.current[0], window.location.search]).toEqual(['all', '?round=2026-10-21']);
    expect(history.length).toBe(entries);
  });

  test('reads the URL, and follows it when the browser navigates back', () => {
    const { result } = renderHook(() => useSearchParam('round', 'latest'));
    const loaded = result.current[0];

    act(() => {
      history.replaceState(null, '', '/arena/');
      window.dispatchEvent(new PopStateEvent('popstate'));
    });

    expect([loaded, result.current[0]]).toEqual(['2026-10-21', 'latest']);
  });

  test.each(['all', '', 'category:all', 'a&b + %'])(
    'distinguishes absent category from the literal %j',
    (category) => {
      const { result } = renderHook(() => useSearchParam('category', null));
      expect(result.current[0]).toBeNull();

      act(() => {
        result.current[1](category);
      });

      expect(result.current[0]).toBe(category);
      expect(new URLSearchParams(window.location.search).get('category')).toBe(category);

      act(() => {
        result.current[1](null);
      });

      expect(result.current[0]).toBeNull();
      expect(window.location.search).toBe('?round=2026-10-21');
    },
  );
});
