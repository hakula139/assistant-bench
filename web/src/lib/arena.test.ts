import { describe, expect, test, vi } from 'vitest';

import { ApiError, listFamilies } from '@/lib/arena';

describe('listFamilies', () => {
  test.each([
    ['the detail of an error response', { detail: 'no outputs to pair' }, 'no outputs to pair'],
    ['the status of an error response without a detail', '<h1>Bad Gateway</h1>', 'HTTP 502'],
  ])('reports %s', async (_, body, message) => {
    vi.stubGlobal('fetch', () =>
      Promise.resolve(
        new Response(typeof body === 'string' ? body : JSON.stringify(body), { status: 502 }),
      ),
    );

    const families = listFamilies();

    await expect(families).rejects.toBeInstanceOf(ApiError);
    await expect(families).rejects.toMatchObject({ message, status: 502 });
  });
});
