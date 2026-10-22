import { readFile } from 'node:fs/promises';

import { defineCollection } from 'astro:content';
import { file, glob } from 'astro/loaders';
import { z } from 'astro/zod';

import { familyConfigSchema } from '@/lib/family';
import { reportSchema } from '@/lib/report';
import { catalogModelSchema, systemConfigSchema } from '@/lib/systems';

const EXPORT_VAR = 'BENCH_NIXOS_CONFIG';

const families = defineCollection({
  loader: glob({
    pattern: '*/family.toml',
    base: '../families',
    generateId: ({ entry }) => entry.split('/')[0],
  }),
  schema: familyConfigSchema,
});

const reports = defineCollection({
  loader: glob({ pattern: '*/*.json', base: '../reports' }),
  schema: reportSchema,
});

const systems = defineCollection({
  loader: file('../systems.toml'),
  schema: systemConfigSchema,
});

/** The catalog models of the nixos-config export that the dev shell names, as `bench` reads it. */
const models = defineCollection({
  loader: async () => {
    const path = process.env[EXPORT_VAR];
    if (!path) {
      throw new Error(`${EXPORT_VAR} is unset. Build the site inside \`nix develop\`.`);
    }
    const exported = z
      .object({ catalog: z.object({ models: z.record(z.string(), catalogModelSchema) }) })
      .parse(JSON.parse(await readFile(path, 'utf8')));
    return Object.entries(exported.catalog.models).map(([id, model]) => ({ id, ...model }));
  },
  schema: catalogModelSchema,
});

export const collections = { families, reports, systems, models };
