// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';

import { VoteApp } from '@/components/vote/VoteApp';
import type { FamilyView, SessionView } from '@/lib/arena';
import { defectTerms } from '@/lib/glossary';
import { defects } from '@/lib/report';

const FAMILY: FamilyView = {
  name: 'fixture',
  aspect: 'writing',
  display_name: 'Chinese',
};

interface Cast {
  token: string;
  choice: string;
  defects: { a: string[]; b: string[] };
  note: string | null;
}

interface StubOptions {
  /** The cases the session `s1` can show, which end it once each has a vote. */
  cases?: number;
  /** The votes `s1` already holds, as a session cast in an earlier visit does. */
  prior?: number;
  brief?: { instruction: string; checklist: string[] };
  /** Serves each output as a page by URL, as a family that renders HTML does. */
  pages?: boolean;
}

/**
 * A stand-in for the arena API that keeps one open pair in session `s1` until it is voted on or
 * undone, and records each session start, resume, vote, and undo.
 */
function stubApi({ cases = 40, prior = 0, brief, pages = false }: StubOptions = {}) {
  const starts: { family: string }[] = [];
  const resumes: string[] = [];
  const casts: Cast[] = [];
  let undos = 0;
  let served = 0;
  let open: string | null = null;

  const votes = () => prior + casts.length - undos;

  const pairOf = (token: string) => ({
    token,
    instruction: `Instruction ${token}`,
    checklist: [],
    ...brief,
    ...(pages
      ? {
          a: { render: 'html' as const, url: `http://127.0.0.1:8791/render/${token}/a` },
          b: { render: 'html' as const, url: `http://127.0.0.1:8791/render/${token}/b` },
        }
      : {
          a: { render: 'text' as const, text: `Output A${token.slice(1)}` },
          b: { render: 'text' as const, text: `Output B${token.slice(1)}` },
        }),
  });

  const view = (): SessionView => {
    const count = votes();
    if (count >= cases) {
      open = null;
    } else {
      open ??= `t${++served}`;
    }
    return {
      id: 's1',
      family: FAMILY,
      votes: count,
      remaining: cases - count,
      pair: open === null ? null : pairOf(open),
    };
  };

  const draw = (): SessionView => {
    open = `t${++served}`;
    return view();
  };

  const respond = (value: unknown, status = 200) =>
    Promise.resolve(new Response(JSON.stringify(value), { status }));

  vi.stubGlobal('fetch', (path: string, init?: RequestInit) => {
    if (path === '/api/families') {
      return respond([FAMILY]);
    }
    if (path === '/api/sessions') {
      const { family } = JSON.parse(init?.body as string) as { family: string };
      starts.push({ family });
      return family === FAMILY.name ? respond(draw(), 201) : respond({ detail: 'not found' }, 404);
    }
    const resume = /^\/api\/sessions\/([^/]+)\/pair$/.exec(path);
    if (resume) {
      resumes.push(resume[1]);
      return resume[1] === 's1' ? respond(view()) : respond({ detail: 'not found' }, 404);
    }
    if (path === '/api/sessions/s1/undo') {
      if (votes() === 0) {
        return respond({ detail: 'no vote to undo' }, 409);
      }
      undos += 1;
      open = null;
      return respond(draw());
    }
    const vote = /^\/api\/pairs\/([^/]+)\/vote$/.exec(path);
    if (vote) {
      if (vote[1] !== open) {
        return respond({ detail: 'not found' }, 404);
      }
      casts.push({ token: vote[1], ...(JSON.parse(init?.body as string) as Omit<Cast, 'token'>) });
      open = null;
      return respond(draw());
    }
    return respond({ detail: `unexpected ${path}` }, 404);
  });
  return {
    starts,
    resumes,
    casts,
    undos: () => undos,
    // Votes the open pair in another tab, which retires it for this one.
    elsewhere: () => {
      casts.push({
        token: open as string,
        choice: 'tie',
        defects: { a: [], b: [] },
        note: null,
      });
      open = null;
      draw();
    },
  };
}

/** The session's status line above the prompt, such as "Writing · Chinese, 2 votes". */
const statusLine = () =>
  screen.getByRole('button', { name: /\d+ votes?$/ }).parentElement?.textContent;

async function startSession(page = <VoteApp />) {
  const user = userEvent.setup();
  render(page);
  await user.click(await screen.findByRole('button', { name: FAMILY.display_name }));
  await screen.findByText('Output A1');
  return user;
}

describe('VoteApp', () => {
  beforeEach(() => {
    localStorage.clear();
    history.replaceState(null, '', '/vote/');
    vi.spyOn(window, 'scrollTo').mockImplementation(() => undefined);
  });

  afterEach(() => {
    vi.useRealTimers();
    cleanup();
  });

  test('submits from the note by chord, then takes the next key as a shortcut', async () => {
    const api = stubApi();
    const user = await startSession();

    await user.keyboard('a{n}');
    await user.keyboard('第一行{Enter}第二行');
    await user.keyboard('{Control>}{Enter}{/Control}');
    await screen.findByText('Output A2');
    await user.keyboard('b');

    expect(api.casts).toEqual([
      { token: 't1', choice: 'a', defects: { a: [], b: [] }, note: '第一行\n第二行' },
    ]);
    expect(screen.getByRole('radio', { name: 'B is better' })).toHaveProperty(
      'ariaChecked',
      'true',
    );
    expect(screen.getByRole('textbox')).toHaveProperty('value', '');
    expect(document.activeElement).toBe(screen.getByRole('group', { name: 'Pair 2' }));
  });

  test('submits on Enter after a click on Undo instead of undoing again', async () => {
    const api = stubApi();
    const user = await startSession();
    await user.keyboard('a{Enter}');
    await screen.findByText('Output A2');
    await user.keyboard('t{Enter}');
    await screen.findByText('Output A3');

    await user.click(screen.getByRole('button', { name: 'Undo' }));
    await screen.findByText('Output A4');
    await user.keyboard('b{Enter}');
    await screen.findByText('Output A5');

    expect(api.undos()).toBe(1);
    expect(api.casts.map((cast) => cast.choice)).toEqual(['a', 'tie', 'b']);
  });

  test('takes a shortcut typed in upper case, and undoes from the completed session', async () => {
    const api = stubApi({ cases: 1 });
    const user = await startSession();

    await user.keyboard('{Shift>}A{/Shift}{Enter}');
    await screen.findByText('Session complete');
    await user.keyboard('u');
    await screen.findByText('Output A3');

    expect(api.casts.map((cast) => cast.choice)).toEqual(['a']);
    expect(api.undos()).toBe(1);
  });

  test('links back to the family list from the completed session', async () => {
    stubApi({ cases: 1 });
    const user = await startSession();

    await user.keyboard('a{Enter}');
    await screen.findByText('Session complete');

    expect(screen.getByRole('link', { name: 'Choose another family' }).getAttribute('href')).toBe(
      '/vote/',
    );
  });

  test('submits the verdict picked and the vote sent by pointer', async () => {
    const api = stubApi();
    const user = await startSession();

    await user.click(screen.getByRole('radio', { name: 'B is better' }));
    await user.click(screen.getByRole('button', { name: /Submit vote/ }));
    await screen.findByText('Output A2');

    expect(api.casts.map((cast) => cast.choice)).toEqual(['b']);
  });

  test('submits on Enter pressed inside an open Issues popover', async () => {
    const api = stubApi();
    const user = await startSession();
    await user.keyboard('a');
    const outputA = screen.getByRole('region', { name: 'Output A' });
    await user.click(within(outputA).getByRole('button', { name: 'Issues' }));
    await user.click(await screen.findByRole('checkbox', { name: 'Too long' }));

    await user.keyboard('{Enter}');
    await screen.findByText('Output A2');

    expect(api.casts).toEqual([
      { token: 't1', choice: 'a', defects: { a: ['length'], b: [] }, note: null },
    ]);
  });

  test('lays out page outputs at the viewport width picked', async () => {
    vi.stubGlobal(
      'ResizeObserver',
      class {
        observe() {}
        disconnect() {}
      },
    );
    stubApi({ pages: true });
    const user = userEvent.setup();
    render(<VoteApp />);
    await user.click(await screen.findByRole('button', { name: FAMILY.display_name }));
    const frame = await screen.findByTitle('Output A');
    expect(frame.style.width).toBe('1280px');

    await user.click(screen.getByRole('radio', { name: '375 px' }));

    expect(screen.getByTitle('Output A').style.width).toBe('375px');
    expect(screen.getByTitle('Output B').style.width).toBe('375px');
  });

  test('starts the family it shows by short name, and names it in full in the session', async () => {
    const api = stubApi({ cases: 1 });
    const user = await startSession();
    expect(statusLine()).toBe('Writing · Chinese, 0 votes');

    await user.keyboard('a{Enter}');
    await screen.findByText('1 vote recorded on Writing · Chinese.');
    await user.click(screen.getByRole('button', { name: 'Start another session' }));

    await vi.waitFor(() => {
      expect(api.starts).toEqual([{ family: FAMILY.name }, { family: FAMILY.name }]);
    });
  });

  test('counts the votes so far, with the pairs left behind the count', async () => {
    stubApi();
    const user = await startSession();

    await user.keyboard('a{Enter}');
    await screen.findByText('Output A2');
    await user.click(screen.getByRole('button', { name: '1 vote' }));

    expect(statusLine()).toBe('Writing · Chinese, 1 vote');
    expect(screen.getByRole('dialog', { name: 'This session' }).textContent).toContain(
      '39 pairs left to vote on.',
    );
  });

  test('shows that each vote saved, then clears the notice', async () => {
    stubApi();
    const user = await startSession();
    await user.keyboard('a');

    vi.useFakeTimers();
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Submit vote/ }));
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(screen.getByRole('status').textContent).toBe('Saved');
    act(() => {
      vi.advanceTimersByTime(2000);
    });
    expect(screen.getByRole('status').textContent).toBe('');
  });

  test('clears the saved notice when a vote is undone', async () => {
    stubApi();
    const user = await startSession();

    await user.keyboard('a{Enter}');
    await screen.findByText('Output A2');
    expect(screen.getByRole('status').textContent).toBe('Saved');

    await user.keyboard('u');
    await screen.findByText('Output A3');

    expect(screen.getByRole('status').textContent).toBe('');
  });

  test('follows the session when another tab has voted on the open pair', async () => {
    const api = stubApi();
    const user = await startSession();
    api.elsewhere();

    await user.keyboard('a{Enter}');
    await screen.findByText('Output A2');

    expect(api.resumes).toEqual(['s1']);
    expect(api.casts.map((cast) => cast.choice)).toEqual(['tie']);
    expect(screen.getByRole('status').textContent).toBe('This pair changed in another tab.');
  });

  test('clears a family the arena does not have, and names it', async () => {
    history.replaceState(null, '', '/vote/?family=unknown');
    localStorage.setItem('bench-session:unknown', 'retired');
    const api = stubApi();

    render(<VoteApp />);

    expect((await screen.findByRole('alert')).textContent).toBe(
      'No family named unknown to vote on.',
    );
    expect(window.location.search).toBe('');
    expect(localStorage.getItem('bench-session:unknown')).toBeNull();
    expect(api.starts).toEqual([{ family: 'unknown' }]);
  });

  test("resumes the family's session from an earlier visit, and again on a reload", async () => {
    localStorage.setItem(`bench-session:${FAMILY.name}`, 's1');
    const api = stubApi({ prior: 3 });
    const user = await startSession();
    const resumed = statusLine();
    await user.keyboard('a{Enter}');
    await screen.findByText('Output A2');
    cleanup();

    render(<VoteApp />);
    await screen.findByText('Output A2');

    expect(resumed).toBe('Writing · Chinese, 3 votes');
    expect(statusLine()).toBe('Writing · Chinese, 4 votes');
    expect(api.resumes).toEqual(['s1', 's1']);
    expect(api.starts).toEqual([]);
  });

  test('starts a session when the arena no longer has the stored one', async () => {
    localStorage.setItem(`bench-session:${FAMILY.name}`, 'retired');
    const api = stubApi();

    await startSession();

    expect(api.resumes).toEqual(['retired']);
    expect(api.starts).toEqual([{ family: FAMILY.name }]);
    expect(localStorage.getItem(`bench-session:${FAMILY.name}`)).toBe('s1');
  });

  test('opens the prompt for each pair, even after the previous one was collapsed', async () => {
    stubApi();
    const user = await startSession();

    await user.click(screen.getByRole('button', { name: 'Prompt' }));
    expect(screen.getByRole('button', { name: 'Prompt' })).toHaveProperty('ariaExpanded', 'false');

    await user.keyboard('a{Enter}');
    await screen.findByText('Output A2');

    expect(screen.getByRole('button', { name: 'Prompt' })).toHaveProperty('ariaExpanded', 'true');
  });

  test('renders the prompt as Markdown and sets the checklist apart under its own heading', async () => {
    stubApi({
      brief: {
        instruction: 'Use only these facts:\n\n- The build passed.\n- `make test` ran.',
        checklist: ['Names `make test` and both facts'],
      },
    });

    await startSession();

    const checklist = screen.getByRole('region', { name: 'What to check' });
    const facts = screen
      .getAllByRole('listitem')
      .filter((item) => !checklist.contains(item))
      .map((item) => item.textContent);
    expect(facts).toEqual(['The build passed.', 'make test ran.']);
    const point = within(checklist).getByRole('listitem');
    expect(point.textContent).toBe('Names make test and both facts');
    expect(point.querySelector('code')?.textContent).toBe('make test');
  });

  test('opens the next pair at the top of the page in fresh output panes', async () => {
    stubApi();
    const user = await startSession();
    const scrollTo = vi.spyOn(window, 'scrollTo');
    scrollTo.mockClear();
    const firstPane = screen.getByRole('region', { name: 'Output A' });

    await user.keyboard('a{Enter}');
    await screen.findByText('Output A2');

    expect(scrollTo).toHaveBeenCalledWith({ top: 0 });
    expect(screen.getByRole('region', { name: 'Output A' })).not.toBe(firstPane);
  });

  test('tags issues on each output by digit or from its Issues button', async () => {
    const api = stubApi();
    const user = await startSession();

    await user.keyboard('2');
    await user.keyboard('0');
    const outputA = screen.getByRole('region', { name: 'Output A' });
    const outputB = screen.getByRole('region', { name: 'Output B' });
    expect(within(outputA).getByRole('button', { name: 'Issues 1' })).toBeDefined();
    await user.click(within(outputB).getByRole('button', { name: 'Issues 1' }));
    expect(screen.getByRole('checkbox', { name: 'Too long' })).toHaveProperty(
      'ariaChecked',
      'true',
    );
    expect(screen.getByRole('checkbox', { name: 'Punctuation' })).toHaveProperty(
      'ariaChecked',
      'false',
    );
    const tone = await screen.findByRole('checkbox', { name: 'Wrong tone' });
    expect(tone.getAttribute('aria-keyshortcuts')).toBe('8');
    expect(screen.getByText('its reader.').className).toBe('text-nowrap');
    await user.click(tone);
    expect(within(outputB).getByRole('button', { name: 'Issues 2' })).toBeDefined();
    for (const defect of defects) {
      const { term, definition } = defectTerms[defect];
      expect(
        screen.queryByRole('checkbox', { name: term, description: definition }),
        defect,
      ).not.toBeNull();
    }
    await user.keyboard('{Escape}');

    await user.click(within(outputA).getByRole('button', { name: 'Issues 1' }));
    expect(screen.getByRole('checkbox', { name: 'Punctuation' })).toHaveProperty(
      'ariaChecked',
      'true',
    );
    expect(
      screen.getByRole('checkbox', { name: 'Too long' }).getAttribute('aria-keyshortcuts'),
    ).toBe('5');
    await user.click(screen.getByRole('checkbox', { name: 'Too long' }));
    expect(within(outputA).getByRole('button', { name: 'Issues 2' })).toBeDefined();
    await user.keyboard('{Escape}');

    await user.keyboard('a{Enter}');
    await screen.findByText('Output A2');
    await user.keyboard('b{Enter}');
    await screen.findByText('Output A3');

    expect(api.casts.map((cast) => cast.defects)).toEqual([
      { a: ['punctuation', 'length'], b: ['tone', 'length'] },
      { a: [], b: [] },
    ]);
  });

  test('leaves the note on Escape, so the next key is a shortcut again', async () => {
    const api = stubApi();
    const user = await startSession();

    await user.keyboard('n');
    await user.keyboard('x{Escape}b{Enter}');
    await screen.findByText('Output A2');

    expect(api.casts).toEqual([{ token: 't1', choice: 'b', defects: { a: [], b: [] }, note: 'x' }]);
  });

  test('leaves Enter on a focused link or button outside the pair to that control', async () => {
    const api = stubApi();
    const user = await startSession(
      <>
        <a href="#methodology">Methodology</a>
        <button type="button">Theme</button>
        <VoteApp />
      </>,
    );

    await user.keyboard('a');
    screen.getByRole('link', { name: 'Methodology' }).focus();
    await user.keyboard('{Enter}');
    screen.getByRole('button', { name: 'Theme' }).focus();
    await user.keyboard('{Enter}');

    expect(api.casts).toEqual([]);
    expect(screen.getByText('Output A1')).toBeDefined();
  });

  test('groups the families by aspect, with those without one last', async () => {
    const families: FamilyView[] = [
      { name: 'smoke-text', aspect: null, display_name: 'Smoke' },
      FAMILY,
      { name: 'writing-en', aspect: 'writing', display_name: 'English' },
    ];
    vi.stubGlobal('fetch', () => Promise.resolve(new Response(JSON.stringify(families))));

    render(<VoteApp />);
    const groups = await screen.findAllByRole('region');

    expect(
      groups.map((group) => [
        within(group).getByRole('heading').textContent,
        within(group)
          .getAllByRole('button')
          .map((button) => button.textContent),
      ]),
    ).toEqual([
      ['Writing', ['Chinese', 'English']],
      ['Unlisted', ['Smoke']],
    ]);
  });

  test.each([
    [
      'the server refuses it',
      () =>
        Promise.resolve(
          new Response(JSON.stringify({ detail: 'no outputs to pair' }), { status: 409 }),
        ),
      'no outputs to pair',
    ],
    [
      'the family is gone',
      () => Promise.resolve(new Response(JSON.stringify({ detail: 'not found' }), { status: 404 })),
      'This family has no outputs to vote on now. Restart uv run bench serve after a run.',
    ],
    [
      'no server answers',
      () => Promise.reject(new TypeError('Failed to fetch')),
      'Could not reach the vote server. Check that uv run bench serve is still running, then reload.',
    ],
    [
      'the response body cannot be read',
      async () => {
        const response = new Response(JSON.stringify({ id: 's1' }));
        await response.text();
        return response;
      },
      'Body is unusable: Body has already been read',
    ],
  ])('shows on the start screen why a session did not start when %s', async (_, start, alert) => {
    vi.stubGlobal('fetch', (path: string) =>
      path === '/api/families' ? Promise.resolve(new Response(JSON.stringify([FAMILY]))) : start(),
    );
    const user = userEvent.setup();
    render(<VoteApp />);

    await user.click(await screen.findByRole('button', { name: FAMILY.display_name }));

    expect((await screen.findByRole('alert')).textContent).toBe(alert);
  });
});
