/**
 * The skills families measure, in the order the site lists them, so a family with a new aspect
 * adds an entry here and its icon in `components/site/Method.tsx`.
 */
export const aspects = {
  'technical-writing': { title: 'Technical Writing' },
} satisfies Record<string, { title: string }>;

export type Aspect = keyof typeof aspects;

export const aspectIds = Object.keys(aspects) as [Aspect, ...Aspect[]];

/** A family's full name: its aspect's title, then its name within the aspect, when it has one. */
export function familyTitle({
  aspect,
  display_name: name,
}: {
  aspect?: Aspect | null;
  display_name: string;
}): string {
  return aspect ? `${aspects[aspect].title} · ${name}` : name;
}
