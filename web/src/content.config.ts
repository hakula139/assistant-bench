import { defineCollection } from 'astro:content';
import { glob } from 'astro/loaders';

import { familyConfigSchema } from '@/lib/family';
import { reportSchema } from '@/lib/report';

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

export const collections = { families, reports };
