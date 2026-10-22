import type { AstroComponentFactory } from 'astro/runtime/server/index.js';

import TestsBoard from '@/components/scorers/tests/Board.astro';
import TestsMethod from '@/components/scorers/tests/Method.astro';
import TestsPanel from '@/components/scorers/tests/Panel.astro';
import VotesBoard from '@/components/scorers/votes/Board.astro';
import VotesMethod from '@/components/scorers/votes/Method.astro';
import VotesPanel from '@/components/scorers/votes/Panel.astro';
import { panelForest } from '@/components/scorers/votes/forest';
import { votesParts } from '@/components/scorers/votes/parts';
import { drawnMarks } from '@/components/site/Forest';
import type { Scorer } from '@/lib/family';
import type { LegendMark } from '@/lib/forest';
import type { Family } from '@/lib/reports';

/** How the site renders the families of one scorer. */
interface ScorerView {
  /** The family page's board, given `family`, with its own state before the first round. */
  Board: AstroComponentFactory;
  /** The home page's compact board, given `family`. */
  Panel: AstroComponentFactory;
  /** The methodology's section body, which the family page links at `#<scorer>`. */
  Method: AstroComponentFactory;
  /** The section's parts by key, each headed at `partId(scorer, key)` and listed in the contents. */
  parts: Record<string, string>;
  /** The legend marks the family's panel draws, which the home page's shared legend names. */
  marks: (family: Family) => LegendMark[];
}

/** Each scorer's views, which every page dispatches through, so a new scorer adds an entry here. */
export const scorerViews = {
  votes: {
    Board: VotesBoard,
    Panel: VotesPanel,
    Method: VotesMethod,
    parts: votesParts,
    marks: (family) => {
      const { rows, domain } = panelForest(family);
      return drawnMarks(rows, domain);
    },
  },
  tests: { Board: TestsBoard, Panel: TestsPanel, Method: TestsMethod, parts: {}, marks: () => [] },
} satisfies Record<Scorer, ScorerView>;
