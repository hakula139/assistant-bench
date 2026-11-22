import { readFile } from 'node:fs/promises';

import { defineCollection } from 'astro:content';
import { file, glob } from 'astro/loaders';
import { z } from 'astro/zod';

import { familyConfigSchema } from '@/lib/family';
import { reportSchema } from '@/lib/report';

/**
 * The field of a `systems.toml` table the site reads. The bench's catalog validates the whole
 * file, including that every display name is unique.
 */
export const systemConfigSchema = z.object({ display_name: z.string().min(1) });

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

const currentReports = defineCollection({
  loader: {
    name: 'arena-release',
    async load(context) {
      const { config, store } = context;
      store.clear();
      const path = new URL('.arena/current.json', config.root);
      const source = await readFile(path, 'utf8').catch((error: unknown) => {
        if (error instanceof Error && 'code' in error && error.code === 'ENOENT') {
          return '{}';
        }
        throw error;
      });
      const id = 'current';
      const reports: unknown = JSON.parse(source);
      const data = await context.parseData({ id, data: { reports } });
      store.set({ id, data });
    },
  },
  schema: z.strictObject({ reports: z.record(z.string(), z.nullable(reportSchema)) }),
});

const systems = defineCollection({
  loader: file('../systems.toml'),
  schema: systemConfigSchema,
});

export const collections = { families, reports, currentReports, systems };
