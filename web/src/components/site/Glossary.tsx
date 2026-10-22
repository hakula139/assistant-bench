import { cn } from 'cn';

import { Badge } from '@/components/ui/badge';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';

export interface GlossaryEntry {
  term: string;
  /** Shows the term as the outline badge the leaderboard marks it with. */
  badge?: boolean;
  definition: string;
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
        {entries.map(({ term, badge, definition }) => (
          <TableRow key={term}>
            <TableCell className={cn(wrapping, 'font-medium')}>
              {badge ? <Badge variant="status">{term}</Badge> : term}
            </TableCell>
            <TableCell className={wrapping}>{definition}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
