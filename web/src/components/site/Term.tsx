import { Anchor, Hourglass, type LucideIcon } from 'lucide-react';
import type { ReactNode } from 'react';

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

/** The marks a board puts beside a system's name, which the methodology glossary repeats. */
export const statusIcons = { preliminary: Hourglass, reference: Anchor } satisfies Partial<
  Record<keyof typeof terms, LucideIcon>
>;

export type Status = keyof typeof statusIcons;

function Definition({ term }: { term: Term }) {
  return (
    <PopoverContent side="top" collisionPadding={16}>
      <PopoverHeader>
        <PopoverTitle>{term.term}</PopoverTitle>
        <PopoverDescription>{term.definition}</PopoverDescription>
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
 * A badge beside a system's name that opens the status's definition. The preliminary badge shows
 * its term, and the reference one only its icon.
 */
export function StatusMark({ status }: { status: Status }) {
  const Icon = statusIcons[status];
  const term = terms[status];
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
