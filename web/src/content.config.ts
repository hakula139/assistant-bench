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

const systems = defineCollection({
  loader: file('../systems.toml'),
  schema: systemConfigSchema,
});

export const collections = { families, reports, systems };
