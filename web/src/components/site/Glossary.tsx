import { cn } from 'cn';

import { statusIcons, type Status } from '@/components/site/Term';
import { Badge } from '@/components/ui/badge';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import type { Term } from '@/lib/glossary';

export interface GlossaryEntry extends Term {
  /** Shows the term beside the badge the leaderboard marks it with. */
  status?: Status;
}

interface GlossaryProps {
  /** Names what the terms are, such as the board's columns. */
  heading: string;
  entries: GlossaryEntry[];
}

const wrapping = 'align-top whitespace-normal';

/** A two-column table of terms and what each means, styled like the boards it explains. */
export function Glossary({ heading, entries }: GlossaryProps) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead className="w-36 sm:w-48">{heading}</TableHead>
          <TableHead>Meaning</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {entries.map(({ term, status, definition }) => {
          const Icon = status && statusIcons[status];
          return (
            <TableRow key={term}>
              <TableCell className={cn(wrapping, 'font-medium text-balance')}>
                <span className="flex items-center gap-2">
                  {Icon && (
                    <Badge variant="status" className="px-1">
                      <Icon aria-hidden="true" />
                    </Badge>
                  )}
                  {term}
                </span>
              </TableCell>
              <TableCell className={cn(wrapping, 'text-balance')}>{definition}</TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}
