import type { Decision } from '@/lib/report';
import { systemName, type SystemNames } from '@/lib/systems';

const allOf = new Intl.ListFormat('en', { type: 'conjunction' });

/** The figure a board's leader tops and the test that separates it, as the note words them. */
export interface DecisionWording {
  figure: string;
  test: string;
}

/** Names the leader and the systems the test cannot separate from it, in the order of `rows`. */
export function decisionNote(
  { leader, tied }: Decision,
  rows: { system: string; name: string }[],
  names: SystemNames,
  { figure, test }: DecisionWording,
): string {
  const top = systemName(names, leader);
  if (tied.length === 0) {
    return `${top} has the top ${figure}, and ${test} separates it from every other system.`;
  }

  const others = rows.filter((row) => tied.includes(row.system)).map((row) => row.name);
  return `${top} has the top ${figure}, but ${test} cannot separate it from ${allOf.format(others)}.`;
}
