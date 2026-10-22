import { z } from 'astro/zod';

/**
 * The fields of a `systems.toml` table the site reads. The pydantic config model validates the
 * whole file, including that every resolved display name is unique.
 */
export const systemConfigSchema = z.object({
  model: z.string().optional(),
  display_name: z.string().optional(),
});

/** The field of a nixos-config catalog model the site reads. */
export const catalogModelSchema = z.object({ name: z.string() });

export type SystemConfig = z.infer<typeof systemConfigSchema>;

/** Display names keyed by `systems.toml` table name. */
export type SystemNames = Record<string, string>;

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

/** Returns a system's display name, failing the build for a system `systems.toml` lacks. */
export function systemName(names: SystemNames, system: string): string {
  if (!Object.hasOwn(names, system)) {
    throw new Error(`system ${system} has no systems.toml table`);
  }
  return names[system];
}
