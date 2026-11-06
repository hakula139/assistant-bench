import type { PublicSystem } from '@/lib/report';

/** Public display names keyed by system ID. */
export type SystemNames = Record<string, string>;

export function reportNames(systems: Record<string, PublicSystem>): SystemNames {
  return Object.fromEntries(
    Object.entries(systems).map(([id, system]) => [id, system.display_name]),
  );
}

export function systemName(names: SystemNames, system: string): string {
  if (!Object.hasOwn(names, system)) {
    throw new Error(`system ${system} has no public display metadata`);
  }
  return names[system];
}
