import { describe, expect, test } from 'vitest';

import { resolveSystemNames, systemName } from '@/lib/systems';

const models = new Map([['acme-large', 'Acme Large']]);

describe('resolveSystemNames', () => {
  test("defaults to the catalog model's name and takes a display name over it", () => {
    const systems = [
      { id: 'large', data: { model: 'acme-large' } },
      { id: 'large-0901', data: { model: 'acme-large', display_name: 'Acme Large 0901' } },
      { id: 'routed', data: { display_name: 'Acme Routed' } },
    ];

    const names = resolveSystemNames(systems, models);

    expect(names).toEqual({
      'large': 'Acme Large',
      'large-0901': 'Acme Large 0901',
      'routed': 'Acme Routed',
    });
  });

  test('rejects a system that nothing names', () => {
    const systems = [{ id: 'ghost', data: { model: 'acme-ghost' } }];

    expect(() => resolveSystemNames(systems, models)).toThrow(
      'system ghost has no display name and no catalog model that names it',
    );
  });
});

describe('systemName', () => {
  test('rejects a system without a table, so no page falls back to its table name', () => {
    expect(() => systemName({ large: 'Acme Large' }, 'retired')).toThrow(
      'system retired has no systems.toml table',
    );
  });
});
