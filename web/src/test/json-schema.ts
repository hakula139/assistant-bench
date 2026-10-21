type Schema = Record<string, unknown>;

const SCALARS = new Set(['boolean', 'integer', 'null', 'number', 'string']);

/**
 * Flattens a JSON Schema into what each path admits: its types, constants and enums, and each
 * object's properties, required keys, extra keys, and the names those keys may take. `$ref`,
 * unions, and type arrays resolve, so pydantic's and Zod's spellings of one schema compare equal,
 * while titles, descriptions, formats, and numeric bounds are ignored. Each union option that a
 * constant property tags, as `scorer` tags a report's results, gets its own paths, such as
 * `$.results<votes>.standings[]`.
 */
export function schemaShape(root: Schema): Record<string, string[]> {
  const defs = (root.$defs ?? {}) as Record<string, Schema | undefined>;
  const shape = new Map<string, Set<string>>();
  const add = (path: string, entry: string) => {
    shape.set(path, (shape.get(path) ?? new Set()).add(entry));
  };

  const resolve = (node: Schema): Schema => {
    if (typeof node.$ref !== 'string') {
      return node;
    }
    const target = defs[node.$ref.replace('#/$defs/', '')];
    if (target === undefined) {
      throw new Error(`unresolved ${node.$ref}`);
    }
    return resolve(target);
  };

  const tag = (option: Schema): string => {
    const properties = (option.properties ?? {}) as Record<string, Schema>;
    const tags = Object.values(properties)
      .map(resolve)
      .flatMap((property) => ('const' in property ? [String(property.const)] : []));
    return tags.length > 0 ? `<${tags.toSorted().join(',')}>` : '';
  };

  const walk = (schema: Schema, path: string): void => {
    const node = resolve(schema);
    const union = (node.anyOf ?? node.oneOf) as Schema[] | undefined;
    if (union) {
      for (const option of union.map(resolve)) {
        walk(option, `${path}${tag(option)}`);
      }
      return;
    }
    if (Array.isArray(node.type)) {
      for (const type of node.type as string[]) {
        walk({ ...node, type }, path);
      }
      return;
    }
    if ('const' in node) {
      add(path, `const ${JSON.stringify(node.const)}`);
    } else if (Array.isArray(node.enum)) {
      add(path, `enum ${JSON.stringify(node.enum.map(String).toSorted())}`);
    } else if (typeof node.type === 'string' && SCALARS.has(node.type)) {
      add(path, node.type);
    } else if (node.type === 'array') {
      add(path, 'array');
      walk((node.items ?? {}) as Schema, `${path}[]`);
    } else if (node.type === 'object' || 'properties' in node) {
      const properties = (node.properties ?? {}) as Record<string, Schema>;
      const extra = node.additionalProperties;
      add(
        path,
        `object ${JSON.stringify({
          properties: Object.keys(properties).toSorted(),
          required: ((node.required ?? []) as string[]).toSorted(),
          extra: typeof extra === 'object' ? 'map' : (extra ?? null),
        })}`,
      );
      for (const [name, property] of Object.entries(properties)) {
        walk(property, `${path}.${name}`);
      }
      if (typeof extra === 'object') {
        walk(extra as Schema, `${path}{}`);
      }
      if (typeof node.propertyNames === 'object') {
        walk(node.propertyNames as Schema, `${path}{key}`);
      }
    } else {
      add(path, `any ${JSON.stringify(node)}`);
    }
  };

  walk(root, '$');
  return Object.fromEntries(
    [...shape]
      .toSorted(([a], [b]) => a.localeCompare(b))
      .map(([path, entries]) => [path, [...entries].toSorted()]),
  );
}
