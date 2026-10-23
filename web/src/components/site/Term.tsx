import { Anchor, Hourglass, type LucideIcon } from 'lucide-react';
import { useId, type ReactNode } from 'react';

import { Badge } from '@/components/ui/badge';
import {
  Popover,
  PopoverContent,
  PopoverDescription,
  PopoverHeader,
  PopoverTitle,
  PopoverTrigger,
} from '@/components/ui/popover';
import { terms, type Term } from '@/lib/glossary';
import { splitTail, TAIL_CLASS } from '@/lib/markdown';

/** One line of text whose last words stay together, so no browser strands one on its own line. */
export function KeptTail({ text }: { text: string }) {
  const [head, tail] = splitTail(text);
  return (
    <>
      {head}
      {tail !== '' && <span className={TAIL_CLASS}>{tail}</span>}
    </>
  );
}

/** The marks a board puts beside a system's name, which the methodology glossary repeats. */
export const statusIcons = { preliminary: Hourglass, anchor: Anchor } satisfies Partial<
  Record<keyof typeof terms, LucideIcon>
>;

export type Status = keyof typeof statusIcons;

function Definition({ term }: { term: Term }) {
  const title = useId();
  return (
    <PopoverContent side="top" collisionPadding={16} aria-labelledby={title}>
      <PopoverHeader>
        <PopoverTitle id={title}>{term.term}</PopoverTitle>
        <PopoverDescription>
          <KeptTail text={term.definition} />
        </PopoverDescription>
      </PopoverHeader>
    </PopoverContent>
  );
}

/**
 * A term with a dotted underline that opens its definition on tap, click, or Enter, for hydrated
 * islands. `children` replaces the visible term, which still names the trigger.
 */
export function Hint({ term, children }: { term: Term; children?: ReactNode }) {
  return (
    <Popover>
      <PopoverTrigger className="cursor-help text-left underline decoration-faint decoration-dotted underline-offset-4">
        {children ?? term.term}
      </PopoverTrigger>
      <Definition term={term} />
    </Popover>
  );
}

/**
 * A badge beside a system's name that opens the status's definition, or `term` when the round
 * gives it one. The preliminary badge shows its term, and the anchor one only its icon.
 */
export function StatusMark({ status, term = terms[status] }: { status: Status; term?: Term }) {
  const Icon = statusIcons[status];
  const labelled = status === 'preliminary';
  return (
    <Popover>
      <Badge asChild variant="status" className={labelled ? undefined : 'px-1'}>
        <PopoverTrigger>
          <Icon aria-hidden="true" />
          <span className={labelled ? undefined : 'sr-only'}>{term.term}</span>
        </PopoverTrigger>
      </Badge>
      <Definition term={term} />
    </Popover>
  );
}
