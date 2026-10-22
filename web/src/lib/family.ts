import { z } from 'astro/zod';

const shared = {
  display_name: z.string(),
  systems: z.array(z.string()).default([]),
  public: z.boolean().default(false),
};

/**
 * The fields of `families/<family>/family.toml` the site reads. The pydantic settings model
 * validates the whole file, so these objects drop the keys they do not list.
 */
export const familyConfigSchema = z.discriminatedUnion('scorer', [
  z.object({ scorer: z.literal('votes'), reference: z.string(), ...shared }),
  z.object({ scorer: z.literal('tests'), ...shared }),
]);

export type FamilyConfig = z.infer<typeof familyConfigSchema>;
