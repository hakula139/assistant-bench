import { describe, expect, test } from 'vitest';

import { reportNames, systemName } from '@/lib/systems';

describe('systemName', () => {
  test('rejects a system without a table, so no page falls back to its table name', () => {
    expect(() => systemName({ large: 'Acme Large' }, 'retired')).toThrow(
      'system retired has no public display metadata',
    );
  });
});

describe('reportNames', () => {
  test('projects each immutable report label, including retired systems', () => {
    const metadata = {
      retired: { display_name: 'Old Public Name', fingerprint: 'synthetic' },
      current: { display_name: 'Current Public Name', fingerprint: null },
    };

    const names = reportNames(metadata);

    expect(names).toEqual({ retired: 'Old Public Name', current: 'Current Public Name' });
  });
});
