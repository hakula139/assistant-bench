import { Anchor, Hourglass, type LucideIcon } from 'lucide-react';
import { useId, type ComponentProps, type ReactNode } from 'react';

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
import { splitTail, TAIL_CLASS } from '@/lib/tail';

export function KeptTail({ text }: { text: string }) {
  const [head, tail] = splitTail(text);

  return (
    <>
      {head}
      {tail !== '' && <span className={TAIL_CLASS}>{tail}</span>}
    </>
  );
}

export const statusIcons = { preliminary: Hourglass, anchor: Anchor } satisfies Partial<
  Record<keyof typeof terms, LucideIcon>
>;

export type Status = keyof typeof statusIcons;

type Side = ComponentProps<typeof PopoverContent>['side'];

function Definition({ term, side = 'top' }: { term: Term; side?: Side }) {
  const title = useId();
  const description = useId();

  return (
    <PopoverContent
      side={side}
      align="start"
      collisionPadding={16}
      aria-labelledby={title}
      aria-describedby={description}
    >
      <PopoverHeader>
        <PopoverTitle id={title}>{term.term}</PopoverTitle>
        <PopoverDescription id={description}>
          <KeptTail text={term.definition} />
        </PopoverDescription>
      </PopoverHeader>
    </PopoverContent>
  );
}

/** Requires hydration. `children` overrides the visible label. */
export function Hint({ term, side, children }: { term: Term; side?: Side; children?: ReactNode }) {
  return (
    <Popover>
      <PopoverTrigger className="cursor-help text-left underline decoration-faint decoration-dotted underline-offset-4">
        {children ?? term.term}
      </PopoverTrigger>
      <Definition term={term} side={side} />
    </Popover>
  );
}

/** `compact` keeps the preliminary mark to its icon, as the anchor's always is, where space is short. */
export function StatusMark({
  status,
  term = terms[status],
  compact = false,
}: {
  status: Status;
  term?: Term;
  compact?: boolean;
}) {
  const Icon = statusIcons[status];
  const labelled = status === 'preliminary' && !compact;

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
