import { z } from 'astro/zod';

import { aspectIds } from '@/lib/aspects';

const shared = {
  aspect: z.enum(aspectIds).optional(),
  display_name: z.string(),
  systems: z.array(z.string()).default([]),
  public: z.boolean().default(false),
};

/** The bounds the votes scorer's gates check beyond a blank or truncated output. */
const gatesSchema = z.object({
  min_chars: z.number().int().optional(),
  max_chars: z.number().int().optional(),
  script: z.literal('hans').optional(),
});

/**
 * The fields of `families/<family>/family.toml` the site reads. The pydantic settings model
 * validates the whole file, so these objects drop the keys they do not list. The site alone knows
 * the aspects, so it rejects an unknown one.
 */
export const familyConfigSchema = z.discriminatedUnion('scorer', [
  z.object({
    scorer: z.literal('votes'),
    reference: z.string(),
    render: z.enum(['text', 'html']).default('text'),
    language: z.enum(['en', 'zh']).optional(),
    gates: gatesSchema.default({}),
    ...shared,
  }),
  z.object({ scorer: z.literal('tests'), ...shared }),
]);

export type FamilyConfig = z.infer<typeof familyConfigSchema>;

export type Scorer = FamilyConfig['scorer'];

export type VotesConfig = Extract<FamilyConfig, { scorer: 'votes' }>;
