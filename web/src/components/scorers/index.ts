import type { AstroComponentFactory } from 'astro/runtime/server/index.js';

import TestsBoard from '@/components/scorers/tests/Board.astro';
import TestsMethod from '@/components/scorers/tests/Method.astro';
import TestsPanel from '@/components/scorers/tests/Panel.astro';
import VotesBoard from '@/components/scorers/votes/Board.astro';
import { panelForest } from '@/components/scorers/votes/forest';
import VotesMethod from '@/components/scorers/votes/Method.astro';
import VotesPanel from '@/components/scorers/votes/Panel.astro';
import { votesParts } from '@/components/scorers/votes/parts';
import TestsThenVotes from '@/components/scorers/votes/TestsThenVotes.astro';
import { drawnMarks } from '@/components/site/Forest';
import type { Scorer } from '@/lib/family';
import type { LegendMark } from '@/lib/forest';
import { formatCountOf } from '@/lib/format';
import type { RenderSection } from '@/lib/method';
import { votesRounds, type Family } from '@/lib/reports';

/** How the site renders the families of one scorer. */
interface ScorerView {
  /**
   * The family page's board, given `family` and the closed `round` to show, with its own state
   * before the first round.
   */
  Board: AstroComponentFactory;
  /** The home page's compact board, given `family`. */
  Panel: AstroComponentFactory;
  /**
   * The methodology's section body, which the family page links at `#<scorer>` unless its render
   * has a section in `renderSections`.
   */
  Method: AstroComponentFactory;
  /** The section's parts by key, each headed at `partId(scorer, key)` and listed in the contents. */
  parts: Record<string, string>;
  /** The legend marks the family's panel draws, which the home page's shared legend names. */
  marks: (family: Family) => LegendMark[];
  /** What the panel's header adds after the latest round's date, such as its vote count. */
  roundDetail: (family: Family) => string | null;
}

/** Each scorer's views, which every page dispatches through, so a new scorer adds an entry here. */
export const scorerViews = {
  votes: {
    Board: VotesBoard,
    Panel: VotesPanel,
    Method: VotesMethod,
    parts: votesParts,
    marks: (family) => {
      const { rows, domain, anchorScore } = panelForest(family);
      return drawnMarks(rows, domain, anchorScore);
    },
    roundDetail: (family) => {
      const round = votesRounds(family).at(-1);
      return round ? formatCountOf(round.results.votes, 'vote') : null;
    },
  },
  tests: {
    Board: TestsBoard,
    Panel: TestsPanel,
    Method: TestsMethod,
    parts: {},
    marks: () => [],
    roundDetail: () => null,
  },
} satisfies Record<Scorer, ScorerView>;

/**
 * The methodology sections on a render's own protocol, after the scorers' sections, which the
 * boards of that render link in place of their scorer's.
 */
export const renderSections = {
  'tests-then-votes': { title: 'Tests, then votes', Method: TestsThenVotes },
} satisfies Record<RenderSection, { title: string; Method: AstroComponentFactory }>;
