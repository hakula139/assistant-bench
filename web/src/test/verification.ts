import { vi } from 'vitest';

import { COMMAND_ID } from '@/test/arena';

export const ADMISSION = {
  command_id: COMMAND_ID,
  action: 'bench_admit' as const,
  sitekey: 'synthetic-site-key',
};

export function stubChallenge() {
  let index = 0;
  const render = vi.fn<NonNullable<Window['turnstile']>['render']>(() => `widget-${++index}`);
  const remove = vi.fn<NonNullable<Window['turnstile']>['remove']>();
  window.turnstile = { render, remove };
  return {
    render,
    remove,
    options: () => {
      const call = render.mock.calls.at(-1);
      if (call === undefined) {
        throw new Error('Expected a managed verification widget');
      }
      return call[1];
    },
  };
}
