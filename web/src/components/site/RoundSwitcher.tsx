import { SwitcherLinks } from '@/components/site/BoardSwitcher';
import { Disclosure } from '@/components/site/Disclosure';
import type { RoundLink } from '@/lib/reports';
import { useSearch } from '@/lib/use-search-param';

/**
 * Names the round on screen and links a family's round pages, given newest first. The links carry
 * the query string after hydration, so the board's filters hold on the next round.
 */
export function RoundSwitcher({ rounds, shown }: { rounds: RoundLink[]; shown: string }) {
  const search = useSearch();

  if (rounds.length < 2) {
    return <p className="text-sm text-muted-foreground">Round {shown}</p>;
  }

  return (
    <nav aria-label="Rounds">
      <Disclosure summary={`Round ${shown}`}>
        <SwitcherLinks
          links={rounds.map(({ round, href }, index) => ({
            href: `${href}${search}`,
            label: index === 0 ? `${round} (latest)` : round,
            current: round === shown,
          }))}
        />
      </Disclosure>
    </nav>
  );
}
