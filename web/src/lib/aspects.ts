/** The skills families measure, in the order the site lists them. */
export const aspects = {
  writing: { title: 'Writing' },
  coding: { title: 'Coding' },
} satisfies Record<string, { title: string }>;

export type Aspect = keyof typeof aspects;

export const aspectIds = Object.keys(aspects) as [Aspect, ...Aspect[]];

export function isAspect(value: string): value is Aspect {
  return Object.hasOwn(aspects, value);
}

/** Private aspect IDs remain visible in family titles. */
export function familyTitle({
  aspect,
  display_name: name,
}: {
  aspect?: string | null;
  display_name: string;
}): string {
  const title = aspect && isAspect(aspect) ? aspects[aspect].title : aspect;

  return title ? `${title} · ${name}` : name;
}

/**
 * Groups registered aspects first, then others in input order, with `null` last.
 */
export function groupByAspect<T extends { aspect: string | null }>(
  items: T[],
): { aspect: T['aspect']; members: T[] }[] {
  const unregistered = [...new Set(items.map(({ aspect }) => aspect))].filter(
    (aspect) => aspect !== null && !isAspect(aspect),
  );

  return [...aspectIds, ...unregistered, null]
    .map((aspect) => ({
      aspect,
      members: items.filter((item) => item.aspect === aspect),
    }))
    .filter(({ members }) => members.length > 0);
}
