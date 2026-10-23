// @vitest-environment jsdom
import { cleanup, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';

import { VoteApp } from '@/components/vote/VoteApp';
import type { FamilyView, SessionView } from '@/lib/arena';

const FAMILY: FamilyView = {
  name: 'prose-zh',
  aspect: 'writing',
  display_name: 'Chinese',
};

interface Cast {
  token: string;
  choice: string;
  flags: string[];
  note: string | null;
}

interface StubOptions {
  /** The cases the session `s1` can show, which end it once each has a vote. */
  cases?: number;
  /** The votes `s1` already holds, as a session cast in an earlier visit does. */
  prior?: number;
  brief?: { instruction: string; checklist: string[] };
}

/**
 * A stand-in for the arena API that serves numbered pairs in session `s1`, each with `brief` when
 * it is given, and records each session start, resume, vote, and undo.
 */
function stubApi({ cases = 40, prior = 0, brief }: StubOptions = {}) {
  const starts: unknown[] = [];
  const resumes: string[] = [];
  const casts: Cast[] = [];
  let undos = 0;
  let served = 0;
  const view = (): SessionView => {
    served += 1;
    const votes = prior + casts.length - undos;
    return {
      id: 's1',
      family: FAMILY,
      votes,
      remaining: cases - votes,
      pair:
        votes === cases
          ? null
          : {
              token: `t${served}`,
              instruction: `Instruction ${served}`,
              checklist: [],
              ...brief,
              a: { render: 'text', text: `Output A${served}` },
              b: { render: 'text', text: `Output B${served}` },
            },
    };
  };
  const respond = (value: unknown, status = 200) =>
    Promise.resolve(new Response(JSON.stringify(value), { status }));

  vi.stubGlobal('fetch', (path: string, init?: RequestInit) => {
    if (path === '/api/families') {
      return respond([FAMILY]);
    }
    if (path === '/api/sessions') {
      starts.push(JSON.parse(init?.body as string));
      return respond(view(), 201);
    }
    const resume = /^\/api\/sessions\/([^/]+)\/pair$/.exec(path);
    if (resume) {
      resumes.push(resume[1]);
      return resume[1] === 's1' ? respond(view()) : respond({ detail: 'not found' }, 404);
    }
    if (path === '/api/sessions/s1/undo') {
      undos += 1;
      return respond(view());
    }
    const vote = /^\/api\/pairs\/([^/]+)\/vote$/.exec(path);
    if (vote) {
      casts.push({ token: vote[1], ...(JSON.parse(init?.body as string) as Omit<Cast, 'token'>) });
      return respond(view());
    }
    return respond({ detail: `unexpected ${path}` }, 404);
  });
  return { starts, resumes, casts, undos: () => undos };
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

  afterEach(cleanup);

  test('submits from the note by chord, then takes the next key as a shortcut', async () => {
    const api = stubApi();
    const user = await startSession();

    await user.keyboard('a{n}');
    await user.keyboard('第一行{Enter}第二行');
    await user.keyboard('{Control>}{Enter}{/Control}');
    await screen.findByText('Output A2');
    await user.keyboard('b');

    expect(api.casts).toEqual([{ token: 't1', choice: 'a', flags: [], note: '第一行\n第二行' }]);
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

  test('starts the family it shows by short name, and names it in full in the session', async () => {
    const api = stubApi({ cases: 1 });
    const user = await startSession();
    expect(statusLine()).toBe('Writing · Chinese, 0 votes');

    await user.keyboard('a{Enter}');
    await screen.findByText('1 vote recorded on Writing · Chinese.');
    await user.click(screen.getByRole('button', { name: 'Start another session' }));

    await vi.waitFor(() => {
      expect(api.starts).toEqual([{ family: 'prose-zh' }, { family: 'prose-zh' }]);
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

    await user.keyboard('a{Enter}');

    expect((await screen.findByRole('status')).textContent).toBe('Saved');
    await vi.waitFor(
      () => {
        expect(screen.getByRole('status').textContent).toBe('');
      },
      { timeout: 3000 },
    );
  });

  test("resumes the family's session from an earlier visit, and again on a reload", async () => {
    localStorage.setItem('bench-session:prose-zh', 's1');
    const api = stubApi({ prior: 3 });
    const user = await startSession();
    const resumed = statusLine();
    await user.keyboard('a{Enter}');
    await screen.findByText('Output A2');
    cleanup();

    render(<VoteApp />);
    await screen.findByText('Output A3');

    expect(resumed).toBe('Writing · Chinese, 3 votes');
    expect(statusLine()).toBe('Writing · Chinese, 4 votes');
    expect(api.resumes).toEqual(['s1', 's1']);
    expect(api.starts).toEqual([]);
  });

  test('starts a session when the arena no longer has the stored one', async () => {
    localStorage.setItem('bench-session:prose-zh', 'retired');
    const api = stubApi();

    await startSession();

    expect(api.resumes).toEqual(['retired']);
    expect(api.starts).toEqual([{ family: 'prose-zh' }]);
    expect(localStorage.getItem('bench-session:prose-zh')).toBe('s1');
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

  test('maps each digit to its flag and each flag button to its own flag', async () => {
    const api = stubApi();
    const user = await startSession();

    await user.keyboard('2');
    await user.click(screen.getByRole('button', { name: 'Register' }));
    await user.keyboard('a{Enter}');
    await screen.findByText('Output A2');

    expect(api.casts.map((cast) => cast.flags)).toEqual([['punctuation', 'register']]);
  });

  test('leaves the note on Escape, so the next key is a shortcut again', async () => {
    const api = stubApi();
    const user = await startSession();

    await user.keyboard('n');
    await user.keyboard('x{Escape}b{Enter}');
    await screen.findByText('Output A2');

    expect(api.casts).toEqual([{ token: 't1', choice: 'b', flags: [], note: 'x' }]);
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
      { name: 'prose-en', aspect: 'writing', display_name: 'English' },
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

  test('shows an error from starting a session on the start screen', async () => {
    vi.stubGlobal('fetch', (path: string) =>
      Promise.resolve(
        path === '/api/families'
          ? new Response(JSON.stringify([FAMILY]))
          : new Response(JSON.stringify({ detail: 'no outputs to pair' }), { status: 409 }),
      ),
    );
    const user = userEvent.setup();
    render(<VoteApp />);

    await user.click(await screen.findByRole('button', { name: FAMILY.display_name }));

    expect((await screen.findByRole('alert')).textContent).toBe('no outputs to pair');
  });
});
