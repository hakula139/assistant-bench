import type { AstroComponentFactory } from 'astro/runtime/server/index.js';

import CheckersBoard from '@/components/scorers/checkers/Board.astro';
import CheckersMethod from '@/components/scorers/checkers/Method.astro';
import TestsBoard from '@/components/scorers/tests/Board.astro';
import TestsMethod from '@/components/scorers/tests/Method.astro';
import TestsPanel from '@/components/scorers/tests/Panel.astro';
import VotesBoard from '@/components/scorers/votes/Board.astro';
import { panelForest } from '@/components/scorers/votes/forest';
import VotesMethod from '@/components/scorers/votes/Method.astro';
import VotesPanel from '@/components/scorers/votes/Panel.astro';
import { votesParts } from '@/components/scorers/votes/parts';
import TestsThenVotes from '@/components/scorers/votes/TestsThenVotes.astro';
import { legendMarks, type LegendMark } from '@/lib/forest';
import { formatCountOf } from '@/lib/format';
import type { RenderSection } from '@/lib/method';
import type { CheckersFamily, Family, TestsFamily, VotesFamily } from '@/lib/reports';

/** How the site renders the families `F` of one scorer. */
interface ScorerView<F extends Family> {
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
  /** The section's parts by key, each headed at `partId(scorer, key)` and listed in contents. */
  parts: Record<string, string>;
  /** The legend marks the family's panel draws, which the home page's shared legend names. */
  marks: (family: F) => LegendMark[];
  /** What the panel's header adds after the latest round's date, such as its vote count. */
  roundDetail: (family: F) => string | null;
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
      return legendMarks(rows, domain, anchorScore);
    },
    roundDetail: (family) => {
      const round = family.rounds.at(-1);
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
  checkers: {
    Board: CheckersBoard,
    Panel: TestsPanel,
    Method: CheckersMethod,
    parts: {},
    marks: () => [],
    roundDetail: () => null,
  },
} satisfies {
  votes: ScorerView<VotesFamily>;
  tests: ScorerView<TestsFamily>;
  checkers: ScorerView<CheckersFamily>;
};

/** Returns the views of a family's scorer, typed for the family. */
export function viewOf<F extends Family>(family: F): ScorerView<F> {
  return scorerViews[family.scorer] as ScorerView<F>;
}

/**
 * The methodology sections on a render's own protocol, after the scorers' sections, which the
 * boards of that render link in place of their scorer's.
 */
export const renderSections = {
  'tests-then-votes': { title: 'Tests, then votes', Method: TestsThenVotes },
} satisfies Record<RenderSection, { title: string; Method: AstroComponentFactory }>;
