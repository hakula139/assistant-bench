import { describe, expect, test } from 'vitest';

import { systemConfigSchema } from '@/content.config';

describe('collection schemas', () => {
  test('keeps only the display name of a systems.toml table', () => {
    const input = { route: 'private-route', effort: 'high', display_name: 'Acme · High' };

    const parsed = systemConfigSchema.parse(input);

    expect(parsed).toEqual({ display_name: 'Acme · High' });
  });

  test('rejects a table without a display name', () => {
    expect(systemConfigSchema.safeParse({ route: 'private-route' }).success).toBe(false);
  });
});
