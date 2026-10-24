import { z } from 'astro/zod';

import type { PublicSystem } from '@/lib/report';

/**
 * The fields of a `systems.toml` table the site reads. The bench's catalog validates the whole
 * file, including that every resolved display name is unique.
 */
export const systemConfigSchema = z.object({
  model: z.string().optional(),
  display_name: z.string().optional(),
});

/** The field of a nixos-config catalog model the site reads. */
export const catalogModelSchema = z.object({ name: z.string() });

export type SystemConfig = z.infer<typeof systemConfigSchema>;

/** Public display names keyed by system ID. */
export type SystemNames = Record<string, string>;

export function reportNames(systems: Record<string, PublicSystem>): SystemNames {
  return Object.fromEntries(
    Object.entries(systems).map(([id, system]) => [id, system.display_name]),
  );
}

/**
 * Resolves each system's display name as the pydantic model does: its own `display_name`, or else
 * its catalog model's name.
 */
export function resolveSystemNames(
  systems: { id: string; data: SystemConfig }[],
  models: Map<string, string>,
): SystemNames {
  return Object.fromEntries(
    systems.map(({ id, data }) => {
      const name = data.display_name ?? (data.model && models.get(data.model));
      if (!name) {
        throw new Error(`system ${id} has no display name and no catalog model that names it`);
      }
      return [id, name];
    }),
  );
}

export function systemName(names: SystemNames, system: string): string {
  if (!Object.hasOwn(names, system)) {
    throw new Error(`system ${system} has no public display metadata`);
  }
  return names[system];
}
