import { SwitcherLinks } from '@/components/site/BoardSwitcher';
import { Disclosure } from '@/components/site/Disclosure';
import type { BoardLink } from '@/lib/reports';
import { useSearch } from '@/lib/use-search-param';

/**
 * Names the round on screen and links a family's round pages, given newest first. The links carry
 * the query string after hydration, so the board's filters hold on the next round.
 */
export function RoundSwitcher({ rounds, summary }: { rounds: BoardLink[]; summary: string }) {
  const search = useSearch();

  if (rounds.length < 2) {
    return <p className="text-sm text-muted-foreground">{summary}</p>;
  }

  return (
    <nav aria-label="Rounds">
      <Disclosure summary={summary}>
        <SwitcherLinks links={rounds.map((link) => ({ ...link, href: `${link.href}${search}` }))} />
      </Disclosure>
    </nav>
  );
}
