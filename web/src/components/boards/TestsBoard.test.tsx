// @vitest-environment jsdom
import { cleanup, render as renderInDom, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderToString } from 'react-dom/server';
import { afterEach, beforeEach, describe, expect, test } from 'vitest';

import { TestsBoard } from '@/components/boards/TestsBoard';
import { passHatKTerm, terms } from '@/lib/glossary';
import { reportSchema } from '@/lib/report';
import type { TestsRound } from '@/lib/reports';
import { readFixture } from '@/test/fixture';
import { stubPlotFrame } from '@/test/plot';

const text = (html: string) => html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');

async function testsRound(): Promise<TestsRound> {
  const { round, closed_at, systems, results } = reportSchema.parse(
    await readFixture('report-tests'),
  );
  if (results.scorer !== 'tests') {
    throw new Error('Expected the tests fixture');
  }
  return { round, closed_at, systems, results };
}

describe('TestsBoard', () => {
  beforeEach(() => {
    stubPlotFrame();
    window.history.replaceState(null, '', '/');
  });
  afterEach(cleanup);

  test("lists the round's systems by pass rate with intervals and pass^2, best first", async () => {
    const html = renderToString(<TestsBoard round={await testsRound()} />);

    expect(text(html)).toContain(
      'Acme Pro 92% 44–100% 83% 6 12 1 $0.2 30s Acme Mini 83% 36–100% 67% 6 12 0 $0.05 12.5s Acme Small 17% 0–64% 0% 6 12 1 $0.01 9s',
    );
  });

  test('names the systems the paired sign test cannot separate from the leader', async () => {
    const html = renderToString(<TestsBoard round={await testsRound()} />);

    expect(text(html)).toContain(
      'Acme Pro has the top pass rate, but a paired sign test over shared tasks cannot separate it from Acme Mini.',
    );
  });

  test('says when the leader is separated from every other system', async () => {
    const round = await testsRound();
    round.results.decision.tied = [];

    const html = renderToString(<TestsBoard round={round} />);

    expect(text(html)).toContain('separates it from every other system.');
  });

  test('leaves out the decision when the round ranks one system', async () => {
    const round = await testsRound();
    round.results.standings = round.results.standings.slice(0, 1);
    round.results.decision.tied = [];

    const html = renderToString(<TestsBoard round={round} />);

    expect(text(html)).toContain('Acme Pro 92%');
    expect(text(html)).not.toContain('paired sign test');
  });

  test("shows a category's own pass rates without family-wide columns or the decision", async () => {
    window.history.replaceState(null, '', '/?category=feature');

    renderInDom(<TestsBoard round={await testsRound()} />);

    const rows = screen.getAllByRole('row').map((row) => row.textContent);
    expect(rows).toEqual([
      'SystemPass rate95% intervalTasks',
      'Acme Mini83%18–100%3',
      'Acme Pro83%18–100%3',
      'Acme Small0%0–71%3',
    ]);
    expect(screen.queryByText(/paired sign test/)).toBeNull();
  });

  test.each([
    ['Pass rate', terms.passRate],
    ['95% interval', terms.passInterval],
    ['pass^2', passHatKTerm(2)],
    ['Tasks', terms.tasks],
    ['Errored', terms.errored],
    ['Cost / trial', terms.testsCostLatency],
    ['p50 latency', terms.testsCostLatency],
  ])("opens the %s column's definition from its header", async (header, term) => {
    renderInDom(<TestsBoard round={await testsRound()} />);

    await userEvent.setup().click(screen.getByRole('button', { name: header }));

    expect((await screen.findByRole('dialog', { name: term.term })).textContent).toContain(
      term.definition,
    );
  });
});
