import { readFile } from 'node:fs/promises';

import { parse } from 'smol-toml';
import { expect, test } from 'vitest';

import { familyConfigSchema } from '@/lib/family';
import { boardPath, listFamilies } from '@/lib/reports';

const read = (path: string) => readFile(new URL(path, import.meta.url), 'utf8');

test("sends each retired family path to the family's board, at any depth, exact paths first", async () => {
  const rules = (await read('../../public/_redirects')).trim().split('\n');
  const ids = [...new Set(rules.map((rule) => rule.split(' ')[0].split('/')[1]))];
  const families = listFamilies(
    await Promise.all(
      ids.map(async (name) => ({
        name,
        config: familyConfigSchema.parse(
          parse(await read(`../../../families/${name}/family.toml`)),
        ),
      })),
    ),
    [],
    {},
  );

  const boards = families.map((family) => [family.name, boardPath(family)]);

  expect(rules).toEqual([
    ...boards.map(([name, board]) => `/${name} ${board} 301`),
    ...boards.map(([name, board]) => `/${name}/* ${board}:splat 301`),
  ]);
});
