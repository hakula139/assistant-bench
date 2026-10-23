import { Hint } from '@/components/site/Term';
import { leaderNote } from '@/lib/glossary';
import type { Decision } from '@/lib/report';

/**
 * A round's leader by display `name`, followed by a hint on how it broke a tie, or a hint on why the
 * round names none.
 */
export function Leader({ decision, name }: { decision: Decision | null; name: string | null }) {
  const note = leaderNote(decision);
  return (
    <span>
      {name}
      {name !== null && note !== null && ', '}
      {note !== null && <Hint term={note}>{name === null ? note.term : 'tied'}</Hint>}
    </span>
  );
}
