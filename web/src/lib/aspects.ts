/** The skills families measure, in the order the site lists them. */
export const aspects = {
  writing: { title: 'Writing' },
} satisfies Record<string, { title: string }>;

export type Aspect = keyof typeof aspects;

export const aspectIds = Object.keys(aspects) as [Aspect, ...Aspect[]];

/** A family's full name: its aspect's title, then its short name, when it has an aspect. */
export function familyTitle({
  aspect,
  display_name: name,
}: {
  aspect?: Aspect | null;
  display_name: string;
}): string {
  return aspect ? `${aspects[aspect].title} · ${name}` : name;
}

/**
 * Groups items by aspect in registry order, keeping each group's items in their given order, with
 * the items that have no aspect last.
 */
export function groupByAspect<T extends { aspect: Aspect | null }>(
  items: T[],
): { aspect: T['aspect']; members: T[] }[] {
  return [...aspectIds, null]
    .map((aspect) => ({
      aspect,
      members: items.filter((item) => item.aspect === aspect),
    }))
    .filter(({ members }) => members.length > 0);
}
