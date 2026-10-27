import { describe, expect, test } from 'vitest';

import { catalogModelSchema, systemConfigSchema } from '@/content.config';

describe('collection schemas', () => {
  test.each([
    [
      'a systems.toml table',
      systemConfigSchema,
      { model: 'acme-large', api: 'openai-responses', effort: 'high', display_name: 'Acme · High' },
      { model: 'acme-large', display_name: 'Acme · High' },
    ],
    [
      'a catalog model',
      catalogModelSchema,
      { name: 'Acme Large', route: 'private-route', channel: 7 },
      { name: 'Acme Large' },
    ],
  ])('keeps only the display fields of %s', (_, schema, input, expected) => {
    expect(schema.parse(input)).toEqual(expected);
  });

  test.each([
    ['a non-string display name', systemConfigSchema, { display_name: 7 }],
    ['a catalog model without a name', catalogModelSchema, { route: 'private-route' }],
  ])('rejects %s', (_, schema, input) => {
    expect(schema.safeParse(input).success).toBe(false);
  });
});
