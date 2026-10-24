import { describe, expect, test } from 'vitest';

import { schemaShape } from '@/test/json-schema';

describe('schemaShape', () => {
  test('recognizes the implicit string type of JSON object property names', () => {
    const schema = { type: 'object', additionalProperties: { type: 'number' } };
    const implicit = { ...schema, propertyNames: { minLength: 1 } };
    const explicit = { ...schema, propertyNames: { type: 'string', minLength: 1 } };

    expect(schemaShape(implicit)).toEqual(schemaShape(explicit));
    expect(schemaShape(implicit)['${key}']).toEqual(['minLength 1', 'string']);
  });

  test('preserves finite key names when normalizing their implicit type', () => {
    const schema = { type: 'object', additionalProperties: { type: 'number' } };

    expect(schemaShape({ ...schema, propertyNames: { enum: ['a', 'b'] } })).not.toEqual(
      schemaShape({ ...schema, propertyNames: { enum: ['a', 'c'] } }),
    );
  });

  test.each([
    [{ minLength: 1 }, { minLength: 2 }],
    [{ maxLength: 3 }, { maxLength: 4 }],
    [{ pattern: '^a' }, { pattern: '^b' }],
  ])('preserves distinct property-name constraints %j and %j', (first, second) => {
    const schema = { type: 'object', additionalProperties: { type: 'number' } };

    expect(schemaShape({ ...schema, propertyNames: first })).not.toEqual(
      schemaShape({ ...schema, propertyNames: second }),
    );
  });
});
