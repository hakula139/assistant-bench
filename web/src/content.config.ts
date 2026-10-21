import { defineCollection } from 'astro:content';
import { glob } from 'astro/loaders';

import { reportSchema } from '@/lib/report';

const reports = defineCollection({
  loader: glob({ pattern: '*/*.json', base: '../reports' }),
  schema: reportSchema,
});

export const collections = { reports };
