import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { MISSING } from '@/lib/format';

export interface FamilySummary {
  name: string;
  round: string;
  rounds: number;
  systems: number;
  leader: string | undefined;
  /** How a leader sharing rank 1 was chosen, as in "tied at rank 1, chosen by the lower cost". */
  tieBreak: string | null;
  /** How the family scores, as in "300 votes" or "Task tests". */
  measure: string;
}

/** The home page's index of families, one row each. */
export function FamiliesTable({ families }: { families: FamilySummary[] }) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Family</TableHead>
          <TableHead>Leader</TableHead>
          <TableHead className="text-right">Systems</TableHead>
          <TableHead>Scored by</TableHead>
          <TableHead>Latest round</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {families.map((family) => (
          <TableRow key={family.name}>
            <TableCell>
              <a href={`/${family.name}/`} className="font-medium">
                {family.name}
              </a>
            </TableCell>
            <TableCell>
              {family.leader ?? MISSING}
              {family.tieBreak !== null && (
                <span className="block text-xs text-muted-foreground">{family.tieBreak}</span>
              )}
            </TableCell>
            <TableCell className="text-right tabular-nums">{family.systems}</TableCell>
            <TableCell>{family.measure}</TableCell>
            <TableCell>
              {family.round}
              {family.rounds > 1 && (
                <span className="text-muted-foreground"> of {family.rounds} rounds</span>
              )}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
