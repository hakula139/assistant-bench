// @vitest-environment jsdom
import type { TurnstileProps } from '@marsidev/react-turnstile';
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { forwardRef, useImperativeHandle } from 'react';
import { afterEach, beforeEach, describe, expect, test, vi, type MockInstance } from 'vitest';

import { VoteApp } from '@/components/vote/VoteApp';
import {
  admissionRequest,
  skipRequest,
  undoRequest,
  voteRequest,
  type FamilyView,
  type PairState,
  type VoteRequest,
} from '@/lib/arena';
import type { Defect } from '@/lib/defects';
import { defectTerms } from '@/lib/glossary';
import { VOTE_FAMILY as FAMILY, pairId, requestBody, WRITING_DEFECTS } from '@/test/arena';

const SITEKEY = 'test-sitekey';

const turnstile = vi.hoisted(() => ({
  props: null as TurnstileProps | null,
  resets: 0,
}));

vi.mock('@marsidev/react-turnstile', () => ({
  Turnstile: forwardRef((props: TurnstileProps, ref) => {
    turnstile.props = props;
    useImperativeHandle(ref, () => ({
      reset: () => {
        turnstile.resets += 1;
      },
    }));
    return <div data-testid="turnstile" />;
  }),
}));

function widget() {
  if (turnstile.props === null) {
    throw new Error('Expected the Turnstile widget');
  }
  return turnstile.props;
}

interface StubOptions {
  cases?: number;
  prior?: number;
  admitted?: boolean;
  brief?: { instruction: string; checklist: string[] };
  render?: 'text' | 'diff';
  defects?: Defect[];
  /** Answers a skip with the same pair, as for a system pair with one case. */
  alone?: boolean;
}

/** A vote API that keeps one pending pair per actor, as the Worker does. */
function stubApi({
  cases = 40,
  prior = 0,
  admitted = true,
  brief,
  render = 'text',
  defects = WRITING_DEFECTS,
  alone = false,
}: StubOptions = {}) {
  const casts: VoteRequest[] = [];
  const undone: VoteRequest[] = [];
  const tokens: string[] = [];
  const pairRequests: string[] = [];
  // Votes recorded in the last 10 minutes, latest last, which undo walks back through.
  const undoable: VoteRequest[] = [];
  let served = 0;
  let open: string | null = null;

  const votes = () => prior + casts.length - undone.length;
  const pairOf = (id: string) => {
    const index = Number(id.slice(-12));
    return {
      id,
      instruction: `Instruction ${index}`,
      checklist: [],
      category: 'message' as const,
      ...brief,
      render,
      a: `Output A${index}`,
      b: `Output B${index}`,
      defects,
    };
  };
  const view = (): PairState => {
    if (votes() >= cases) {
      open = null;
    } else {
      open ??= pairId(++served);
    }
    return {
      pair: open === null ? null : pairOf(open),
      votes: votes(),
      remaining: cases - votes(),
      undo: undoable.length > 0,
    };
  };

  const respond = (value: unknown, status = 200) =>
    Promise.resolve(
      status === 204 ? new Response(null, { status }) : Response.json(value, { status }),
    );
  const refuse = (error: string, status: number) =>
    respond(error === 'admission_required' ? { error, sitekey: SITEKEY } : { error }, status);

  const fetch = vi.fn((path: string, init?: RequestInit) => {
    const url = new URL(path, 'https://site.example');
    const route = `${init?.method ?? 'GET'} ${url.pathname}`;
    if (route === 'GET /api/families') {
      return respond({ families: [FAMILY] });
    }
    if (route === 'POST /api/actors') {
      tokens.push(admissionRequest.parse(requestBody(init)).token);
      admitted = true;
      return respond(null, 204);
    }
    if (!admitted) {
      return refuse('admission_required', 401);
    }
    if (route === 'GET /api/pair') {
      const family = url.searchParams.get('family') ?? '';
      pairRequests.push(family);
      return family === FAMILY.family ? respond(view()) : refuse('no_round', 404);
    }
    if (route === 'POST /api/pair/skip') {
      const { pair } = skipRequest.parse(requestBody(init));
      if (pair !== open) {
        return refuse('stale_pair', 409);
      }
      if (!alone) {
        open = pairId(++served);
      }
      return respond(view());
    }
    if (route === 'POST /api/votes') {
      const request = voteRequest.parse(requestBody(init));
      if (request.pair !== open) {
        return refuse('stale_pair', 409);
      }
      casts.push(request);
      undoable.push(request);
      open = null;
      return respond(null, 204);
    }
    if (route === 'POST /api/votes/undo') {
      undoRequest.parse(requestBody(init));
      const last = undoable.pop();
      if (last === undefined) {
        return refuse('nothing_to_undo', 409);
      }
      undone.push(last);
      open = last.pair;
      return respond(null, 204);
    }
    throw new Error(`Unexpected API route: ${route}`);
  });
  vi.stubGlobal('fetch', fetch);

  return {
    fetch,
    casts,
    undone,
    tokens,
    pairRequests,
    /** Votes the pending pair from another tab, which leaves this tab's copy stale. */
    elsewhere: () => {
      if (open === null) {
        throw new Error('Expected a pending pair');
      }
      casts.push({ pair: open, choice: 'tie', defects: { a: [], b: [] } });
      open = null;
    },
    /** Lets the undo window pass for every vote so far. */
    expire: () => {
      undoable.length = 0;
    },
  };
}

/** Answers one route with `answer` while the stub serves every other request. */
function override(api: ReturnType<typeof stubApi>, path: string, answer: () => Promise<Response>) {
  const fetch = api.fetch.getMockImplementation();
  api.fetch.mockImplementation((url, init) =>
    url.startsWith(path) ? answer() : (fetch?.(url, init) ?? Promise.reject(new Error('No stub'))),
  );
}

const statusLine = () =>
  screen.getByRole('button', { name: /\d+ votes?$/ }).parentElement?.textContent;

const pane = (side: 'A' | 'B') => screen.getByRole('region', { name: `Output ${side}` });

async function startVoting(page = <VoteApp />) {
  const user = userEvent.setup();
  render(page);
  await user.click(await screen.findByRole('button', { name: FAMILY.display_name }));
  await screen.findByText('Output A1');
  return user;
}

describe('VoteApp', () => {
  let scrollTo: MockInstance<typeof window.scrollTo>;

  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
    turnstile.props = null;
    turnstile.resets = 0;
    history.replaceState(null, '', '/vote/');
    scrollTo = vi.spyOn(window, 'scrollTo').mockImplementation(() => undefined);
  });

  afterEach(() => {
    vi.useRealTimers();
    cleanup();
  });

  test.each([null, FAMILY.family])(
    'verifies a fresh browser through Turnstile and continues family %s',
    async (family) => {
      const api = stubApi({ admitted: false });
      history.replaceState(null, '', `/vote/${family === null ? '' : `?family=${family}`}`);
      const user = userEvent.setup();

      render(<VoteApp />);
      if (family === null) {
        await user.click(await screen.findByRole('button', { name: FAMILY.display_name }));
      }
      await screen.findByTestId('turnstile');
      expect(widget().siteKey).toBe(SITEKEY);
      act(() => {
        widget().onSuccess?.('fresh-proof');
      });
      await screen.findByText('Output A1');

      expect(api.tokens).toEqual(['fresh-proof']);
      expect(location.search).toBe(`?family=${FAMILY.family}`);
      expect(api.casts).toEqual([]);
      expect(screen.queryByRole('region', { name: 'Browser verification' })).toBeNull();
    },
  );

  test.each([
    ['failed', () => widget().onError?.('600010'), 'Verification is unavailable. Try again.'],
    ['expired', () => widget().onExpire?.('token'), 'Verification expired. Try again.'],
    [
      'could not load its script',
      () => widget().scriptOptions?.onError?.(),
      'Verification is unavailable. Try again.',
    ],
  ])('offers to retry a challenge that %s', async (_, fail, message) => {
    stubApi({ admitted: false });
    history.replaceState(null, '', `/vote/?family=${FAMILY.family}`);
    const user = userEvent.setup();
    render(<VoteApp />);
    await screen.findByTestId('turnstile');

    act(() => {
      fail();
    });
    expect(await screen.findByText(message)).toBeDefined();
    await user.click(screen.getByRole('button', { name: 'Retry verification' }));

    expect(turnstile.resets).toBe(1);
    expect(screen.queryByText(message)).toBeNull();
  });

  test('asks for a solve only once the challenge needs one', async () => {
    stubApi({ admitted: false });
    history.replaceState(null, '', `/vote/?family=${FAMILY.family}`);
    render(<VoteApp />);
    await screen.findByTestId('turnstile');

    act(() => {
      widget().onBeforeInteractive?.();
    });

    expect(screen.getByText('Verify you are human to continue.')).toBeDefined();
  });

  test('sends a browser that cannot run the challenge elsewhere, with nothing to retry', async () => {
    stubApi({ admitted: false });
    history.replaceState(null, '', `/vote/?family=${FAMILY.family}`);
    render(<VoteApp />);
    await screen.findByTestId('turnstile');

    act(() => {
      widget().onUnsupported?.();
    });

    expect(
      screen.getByText('This browser cannot complete verification. Try another browser.'),
    ).toBeDefined();
    expect(screen.queryByRole('button', { name: 'Retry verification' })).toBeNull();
  });

  test('says voting needs cookies when the browser is still unknown after its admission', async () => {
    const api = stubApi({ admitted: false });
    override(api, '/api/actors', () => Promise.resolve(new Response(null, { status: 204 })));
    history.replaceState(null, '', `/vote/?family=${FAMILY.family}`);
    render(<VoteApp />);
    await screen.findByTestId('turnstile');

    act(() => {
      widget().onSuccess?.('proof');
    });

    expect((await screen.findByRole('alert')).textContent).toBe(
      'Voting needs cookies. Allow cookies for this site, then reload the page.',
    );
    expect(screen.queryByTestId('turnstile')).toBeNull();
    expect(api.fetch.mock.calls.filter(([path]) => path === '/api/actors')).toHaveLength(1);
  });

  test('reports a challenge the Worker rejects without admitting the browser', async () => {
    const api = stubApi({ admitted: false });
    override(api, '/api/actors', () =>
      Promise.resolve(Response.json({ error: 'challenge_failed' }, { status: 400 })),
    );
    history.replaceState(null, '', `/vote/?family=${FAMILY.family}`);
    render(<VoteApp />);
    await screen.findByTestId('turnstile');

    act(() => {
      widget().onSuccess?.('rejected-proof');
    });

    expect(await screen.findByText('Verification failed. Try again.')).toBeDefined();
    expect(screen.getByRole('button', { name: 'Retry verification' })).toBeDefined();
    expect(screen.queryByText('Output A1')).toBeNull();
  });

  test('loads the pair on mount without posting or touching localStorage', async () => {
    const api = stubApi();
    history.replaceState(null, '', `/vote/?family=${FAMILY.family}`);

    render(<VoteApp />);
    await screen.findByText('Output A1');

    expect(api.fetch.mock.calls.map(([, init]) => init?.method)).toEqual(['GET', 'GET']);
    expect(localStorage.length).toBe(0);
  });

  test('shows the choice, issues, Markdown, collapsed prompt and scroll again after a reload', async () => {
    const api = stubApi();
    const user = await startVoting();
    await user.keyboard('b1');
    await user.click(screen.getByRole('button', { name: 'Markdown' }));
    await user.click(screen.getByRole('button', { name: 'Prompt' }));
    vi.spyOn(window, 'scrollY', 'get').mockReturnValue(120);
    const scroller = pane('B').lastElementChild;
    if (scroller === null) {
      throw new Error('Expected the scroll container of output B');
    }
    scroller.scrollTop = 40;
    window.dispatchEvent(new Event('pagehide'));
    cleanup();
    scrollTo.mockClear();

    render(<VoteApp />);
    await screen.findByText('Output A1');

    expect(screen.getByRole('radio', { name: 'B is better' })).toHaveProperty(
      'ariaChecked',
      'true',
    );
    expect(within(pane('A')).getByRole('button', { name: 'Issues 1' })).toBeDefined();
    expect(screen.getByRole('button', { name: 'Markdown' })).toHaveProperty('ariaPressed', 'true');
    expect(screen.getByRole('button', { name: 'Prompt' })).toHaveProperty('ariaExpanded', 'false');
    expect(scrollTo).toHaveBeenLastCalledWith({ top: 120 });
    expect(pane('B').lastElementChild?.scrollTop).toBe(40);
    expect(api.casts).toEqual([]);
  });

  test.each([
    ['a choice', 'a', 'The pair you were choosing on changed, so your choice was cleared.'],
    ['no choice', null, ''],
  ] as const)(
    'drops a draft for another pair, with %s, and says so only for an unsent choice',
    async (_, choice, notice) => {
      stubApi();
      sessionStorage.setItem(
        'bench-vote',
        JSON.stringify({
          family: FAMILY.family,
          pair: pairId(99),
          choice,
          defects: { a: ['facts'], b: [] },
          markdown: false,
          collapsed: false,
          scroll: { window: 0, a: 0, b: 0 },
        }),
      );
      history.replaceState(null, '', `/vote/?family=${FAMILY.family}`);

      render(<VoteApp />);
      await screen.findByText('Output A1');

      expect(screen.getByRole('radio', { name: 'A is better' })).toHaveProperty(
        'ariaChecked',
        'false',
      );
      expect(within(pane('A')).getByRole('button', { name: 'Issues' })).toBeDefined();
      expect(screen.getByRole('status').textContent).toBe(notice);
      expect(JSON.parse(sessionStorage.getItem('bench-vote') ?? '')).toMatchObject({
        pair: pairId(1),
        choice: null,
      });
    },
  );

  test('removes a draft that no longer parses', async () => {
    stubApi();
    sessionStorage.setItem('bench-vote', '{"family": 1}');
    history.replaceState(null, '', `/vote/?family=${FAMILY.family}`);

    render(<VoteApp />);
    await screen.findByText('Output A1');

    expect(JSON.parse(sessionStorage.getItem('bench-vote') ?? '')).toMatchObject({
      family: FAMILY.family,
      pair: pairId(1),
    });
  });

  test('skips to another pair without voting, clearing the unsent choice', async () => {
    const api = stubApi();
    const user = await startVoting();
    await user.keyboard('a');

    await user.keyboard('s');
    await screen.findByText('Output A2');

    expect(screen.getByRole('radio', { name: 'A is better' })).toHaveProperty(
      'ariaChecked',
      'false',
    );
    expect(api.casts).toEqual([]);
  });

  test('loads the current pair with a notice when a skip finds this one gone', async () => {
    const api = stubApi();
    const user = await startVoting();
    api.elsewhere();

    await user.keyboard('s');
    await screen.findByText('Output A2');

    expect(screen.getByRole('status').textContent).toBe(
      'This pair changed. Review the current outputs and choose again.',
    );
  });

  test('keeps the pair and reports a skip that fails', async () => {
    const api = stubApi();
    const user = await startVoting();
    override(api, '/api/pair/skip', () => Promise.reject(new TypeError('Failed to fetch')));

    await user.keyboard('s');

    expect((await screen.findByRole('alert')).textContent).toBe(
      'The voting service is unavailable. Try again.',
    );
    expect(screen.getByText('Output A1')).toBeDefined();
  });

  test('keeps the pair and says so when a skip finds no other case', async () => {
    stubApi({ alone: true });
    const user = await startVoting();

    await user.click(screen.getByRole('button', { name: /Skip/ }));

    expect(await screen.findByText('No other pair is available for these outputs.')).toBeDefined();
    expect(screen.getByText('Output A1')).toBeDefined();
  });

  test('resends the identical vote on Retry after a lost response, locking the ballot meanwhile', async () => {
    const api = stubApi();
    const user = await startVoting();
    const fetch = api.fetch.getMockImplementation();
    let lost = true;
    api.fetch.mockImplementation((path, init) => {
      if (path === '/api/votes' && lost) {
        lost = false;
        return Promise.reject(new TypeError('The vote response was lost'));
      }
      return fetch?.(path, init) ?? Promise.reject(new Error('No stub'));
    });
    await user.keyboard('b1{Enter}');
    await screen.findByRole('button', { name: 'Retry vote' });

    await user.keyboard('a2s');
    expect(screen.getByRole('radio', { name: 'B is better' })).toHaveProperty(
      'ariaChecked',
      'true',
    );
    await user.click(screen.getByRole('button', { name: 'Retry vote' }));
    await screen.findByText('Output A2');

    const sent = api.fetch.mock.calls
      .filter(([path]) => path === '/api/votes')
      .map(([, init]) => requestBody(init));
    expect(sent).toEqual([sent[0], sent[0]]);
    expect(api.casts).toEqual([{ pair: pairId(1), choice: 'b', defects: { a: ['scope'], b: [] } }]);
  });

  test('unlocks the ballot when a retried vote finds its pair gone', async () => {
    const api = stubApi();
    const user = await startVoting();
    const fetch = api.fetch.getMockImplementation();
    let lost = true;
    api.fetch.mockImplementation((path, init) => {
      if (path === '/api/votes' && lost) {
        lost = false;
        return Promise.reject(new TypeError('The vote response was lost'));
      }
      return fetch?.(path, init) ?? Promise.reject(new Error('No stub'));
    });
    await user.keyboard('b{Enter}');
    await screen.findByRole('button', { name: 'Retry vote' });
    api.elsewhere();

    await user.click(screen.getByRole('button', { name: 'Retry vote' }));
    await screen.findByText('Output A2');

    expect(screen.queryByRole('button', { name: 'Retry vote' })).toBeNull();
    expect(screen.getByRole('radio', { name: 'A is better' })).toHaveProperty('disabled', false);
    expect(screen.getByRole('status').textContent).toBe(
      'This pair changed. Review the current outputs and choose again.',
    );
  });

  test('forgets a sent choice even when the next pair fails to load', async () => {
    const api = stubApi();
    const user = await startVoting();
    await user.keyboard('a');
    override(api, '/api/pair', () => Promise.reject(new TypeError('Failed to fetch')));

    await user.keyboard('{Enter}');
    await screen.findByRole('heading', { name: 'Choose a family to vote on' });

    expect(api.casts.map((cast) => cast.choice)).toEqual(['a']);
    expect(JSON.parse(sessionStorage.getItem('bench-vote') ?? '')).toMatchObject({
      choice: null,
    });
  });

  test('tells a network at its daily cap when it can vote again', async () => {
    const api = stubApi();
    const user = await startVoting();
    override(api, '/api/votes', () =>
      Promise.resolve(Response.json({ error: 'daily_limit' }, { status: 429 })),
    );

    await user.keyboard('a{Enter}');

    expect((await screen.findByRole('alert')).textContent).toBe(
      'This network has reached its daily limit. Try again tomorrow.',
    );
  });

  test('loads the current pair with a notice when another tab has voted on this one', async () => {
    const api = stubApi();
    const user = await startVoting();
    api.elsewhere();

    await user.keyboard('a{Enter}');
    await screen.findByText('Output A2');

    expect(api.casts.map((cast) => cast.choice)).toEqual(['tie']);
    expect(screen.getByRole('status').textContent).toBe(
      'This pair changed. Review the current outputs and choose again.',
    );
    expect(screen.queryByRole('button', { name: 'Retry vote' })).toBeNull();
  });

  test('offers Undo while the server allows it, restoring the same pair', async () => {
    const api = stubApi();
    const user = await startVoting();
    expect(screen.getByRole('button', { name: /Undo/ })).toHaveProperty('disabled', true);
    await user.keyboard('a{Enter}');
    await screen.findByText('Output A2');

    await user.click(screen.getByRole('button', { name: /Undo/ }));
    await screen.findByText('Output A1');

    expect(screen.getByRole('status').textContent).toBe('Vote withdrawn.');
    expect(api.undone.map((vote) => vote.pair)).toEqual([pairId(1)]);
    expect(screen.getByRole('button', { name: /Undo/ })).toHaveProperty('disabled', true);
  });

  test('explains an undo the server refuses once the window has passed', async () => {
    const api = stubApi();
    const user = await startVoting();
    await user.keyboard('a{Enter}');
    await screen.findByText('Output A2');
    api.expire();

    await user.keyboard('u');

    expect(
      await screen.findByText('No vote from the last 10 minutes is left to undo.'),
    ).toBeDefined();
    expect(screen.getByText('Output A2')).toBeDefined();
    expect(screen.getByRole('button', { name: /Undo/ })).toHaveProperty('disabled', true);
  });

  test('explains an empty family list', async () => {
    vi.stubGlobal('fetch', () => Promise.resolve(Response.json({ families: [] })));

    render(<VoteApp />);

    await screen.findByText(/No .* (?:available|vote on)/);
    expect(document.body.textContent).not.toContain('uv run');
    expect(screen.getByRole('heading', { name: 'Choose a family to vote on' })).toBeDefined();
  });

  test('submits on Enter after a click on Undo instead of undoing again', async () => {
    const api = stubApi();
    const user = await startVoting();
    await user.keyboard('a{Enter}');
    await screen.findByText('Output A2');
    await user.keyboard('t{Enter}');
    await screen.findByText('Output A3');

    await user.click(screen.getByRole('button', { name: /Undo/ }));
    await screen.findByText('Output A2');
    await user.keyboard('b{Enter}');
    await screen.findByText('Output A4');

    expect(api.undone).toHaveLength(1);
    expect(api.casts.map((cast) => cast.choice)).toEqual(['a', 'tie', 'b']);
  });

  test("submits on Enter from the layout's main, which the skip link and margin clicks focus", async () => {
    const api = stubApi();
    const user = await startVoting(
      <main tabIndex={-1}>
        <VoteApp />
      </main>,
    );
    await user.keyboard('a');
    screen.getByRole('main').focus();

    await user.keyboard('{Enter}');
    await screen.findByText('Output A2');

    expect(api.casts.map((cast) => cast.choice)).toEqual(['a']);
  });

  test('takes a shortcut typed in upper case, and undoes from the completed family', async () => {
    const api = stubApi({ cases: 1 });
    const user = await startVoting();

    await user.keyboard('{Shift>}A{/Shift}{Enter}');
    await screen.findByText('No pairs left');
    await user.keyboard('u');
    await screen.findByText('Output A1');

    expect(api.casts.map((cast) => cast.choice)).toEqual(['a']);
    expect(api.undone).toHaveLength(1);
  });

  test('opens the family list from its links, even in a tab that voted in a family', async () => {
    const api = stubApi({ cases: 1 });
    const user = await startVoting();
    await user.keyboard('a{Enter}');
    await screen.findByText('No pairs left');
    cleanup();
    history.replaceState(null, '', '/vote/');

    render(<VoteApp />);

    expect(await screen.findByRole('button', { name: FAMILY.display_name })).toBeDefined();
    expect(api.pairRequests).toEqual([FAMILY.family, FAMILY.family]);
  });

  test('carries the Markdown view to the next pair and through a reload', async () => {
    const api = stubApi();
    const user = await startVoting();
    await user.click(screen.getByRole('button', { name: 'Markdown' }));
    screen.getByRole('group', { name: 'Pair 1' }).focus();

    await user.keyboard('a{Enter}');
    await screen.findByText('Output A2');
    const afterVote = screen.getByRole('button', { name: 'Markdown' }).ariaPressed;
    api.elsewhere();
    cleanup();
    render(<VoteApp />);
    await screen.findByText('Output A3');

    expect(afterVote).toBe('true');
    expect(screen.getByRole('button', { name: 'Markdown' })).toHaveProperty('ariaPressed', 'true');
  });

  test('counts the recorded votes and links back to the family list once no pair is left', async () => {
    stubApi({ cases: 1 });
    const user = await startVoting();

    await user.keyboard('a{Enter}');
    await screen.findByText('No pairs left');

    expect(screen.getByText('1 vote recorded on Writing · Chinese.')).toBeDefined();
    expect(screen.getByRole('link', { name: 'Choose another family' }).getAttribute('href')).toBe(
      '/vote/',
    );
  });

  test('submits the verdict picked and the vote sent by pointer', async () => {
    const api = stubApi();
    const user = await startVoting();

    expect(screen.queryByTestId('turnstile')).toBeNull();

    await user.click(screen.getByRole('radio', { name: 'B is better' }));
    await user.click(screen.getByRole('button', { name: /Submit vote/ }));
    await screen.findByText('Output A2');

    expect(api.casts.map((cast) => cast.choice)).toEqual(['b']);
  });

  test('leaves Enter and Space on an issue checkbox to the checkbox', async () => {
    const api = stubApi();
    const user = await startVoting();
    await user.keyboard('a');
    await user.click(within(pane('A')).getByRole('button', { name: 'Issues' }));
    const checkbox = await screen.findByRole('checkbox', { name: 'Too long' });
    await user.click(checkbox);

    await user.keyboard('{Enter}');

    expect(api.casts).toEqual([]);
    expect(screen.getByRole('dialog', { name: 'Issues in output A' })).toBeDefined();
    expect(checkbox).toHaveProperty('ariaChecked', 'true');

    await user.keyboard(' ');

    expect(checkbox).toHaveProperty('ariaChecked', 'false');
    expect(api.casts).toEqual([]);
  });

  test.each(['Issues', 'Prompt', 'Markdown'])(
    'activates the focused %s control on Enter without casting the selected verdict',
    async (name) => {
      const api = stubApi();
      const user = await startVoting();
      await user.keyboard('a');
      const control =
        name === 'Issues'
          ? within(pane('A')).getByRole('button', { name })
          : screen.getByRole('button', { name });
      control.focus();

      await user.keyboard('{Enter}');

      expect(api.casts).toEqual([]);
      expect(screen.getByRole('group', { name: 'Pair 1' })).toBeDefined();
      if (name === 'Issues') {
        expect(screen.getByRole('dialog', { name: 'Issues in output A' })).toBeDefined();
      } else if (name === 'Prompt') {
        expect(control).toHaveProperty('ariaExpanded', 'false');
      } else {
        expect(control).toHaveProperty('ariaPressed', 'true');
      }
    },
  );

  test.each<[string, KeyboardEventInit]>([
    ['repeated', { repeat: true }],
    ['composing', { isComposing: true }],
    ['handled', {}],
    ['modified', { ctrlKey: true }],
  ])('ignores %s shortcut events in the comparison', async (scenario, options) => {
    const api = stubApi();
    const user = await startVoting();
    await user.keyboard('t{Enter}');
    await screen.findByText('Output A2');
    const pair = screen.getByRole('group', { name: /^Pair / });

    for (const key of ['a', '1', 's', 'u']) {
      const event = new KeyboardEvent('keydown', {
        key,
        bubbles: true,
        cancelable: true,
        ...options,
      });
      if (scenario === 'handled') {
        event.preventDefault();
      }
      fireEvent(pair, event);
    }

    expect(screen.getByRole('radio', { name: 'A is better' })).toHaveProperty(
      'ariaChecked',
      'false',
    );
    expect(within(pane('A')).getByRole('button', { name: 'Issues' })).toBeDefined();
    expect(screen.getByText('Output A2')).toBeDefined();
    expect(api.undone).toEqual([]);
    expect(api.casts).toHaveLength(1);
  });

  test('keeps the submitted ballot unchanged while its request is pending', async () => {
    const api = stubApi();
    const user = await startVoting();
    await user.keyboard('t{Enter}');
    await screen.findByText('Output A2');
    const fetch = api.fetch.getMockImplementation();
    const pending = Promise.withResolvers<undefined>();
    api.fetch.mockImplementation(async (path, init) => {
      if (path === '/api/votes') {
        await pending.promise;
      }
      return fetch?.(path, init) ?? Promise.reject(new Error('No stub'));
    });
    await user.keyboard('a');

    await user.click(screen.getByRole('button', { name: /Submit vote/ }));
    const pair = screen.getByRole('group', { name: /^Pair / });
    pair.focus();
    await user.keyboard('b1su{Enter}');

    expect(screen.getByRole('radio', { name: 'A is better' })).toHaveProperty(
      'ariaChecked',
      'true',
    );
    expect(document.activeElement).toBe(pair);
    expect(api.undone).toEqual([]);

    pending.resolve(undefined);
    await screen.findByText('Output A3');

    expect(api.casts.at(-1)).toEqual({ pair: pairId(2), choice: 'a', defects: { a: [], b: [] } });
  });

  test('names the family in full, and counts the votes with the pairs left behind the count', async () => {
    stubApi({ prior: 2 });
    const user = await startVoting();
    expect(statusLine()).toBe('Writing · Chinese, 2 votes');

    await user.keyboard('a{Enter}');
    await screen.findByText('Output A2');
    await user.click(screen.getByRole('button', { name: '3 votes' }));

    expect(statusLine()).toBe('Writing · Chinese, 3 votes');
    expect(screen.getByRole('dialog', { name: 'Your votes' }).textContent).toContain(
      '37 pairs left to vote on.',
    );
  });

  test('shows that each vote saved, then clears the notice', async () => {
    stubApi();
    const user = await startVoting();
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

  test.each([
    { next: 'a new prompt', brief: undefined, expanded: 'true' },
    {
      next: 'the same prompt',
      brief: { instruction: 'Shared instruction', checklist: [] },
      expanded: 'false',
    },
  ])(
    'shows $next expanded: $expanded after the prompt is collapsed',
    async ({ brief, expanded }) => {
      stubApi({ brief });
      const user = await startVoting();

      await user.click(screen.getByRole('button', { name: 'Prompt' }));
      expect(screen.getByRole('button', { name: 'Prompt' })).toHaveProperty(
        'ariaExpanded',
        'false',
      );
      screen.getByRole('group', { name: 'Pair 1' }).focus();

      await user.keyboard('a{Enter}');
      await screen.findByText('Output A2');

      expect(screen.getByRole('button', { name: 'Prompt' })).toHaveProperty(
        'ariaExpanded',
        expanded,
      );
    },
  );

  test('renders the prompt as Markdown and sets the checklist apart under its own heading', async () => {
    stubApi({
      brief: {
        instruction: 'Use only these facts:\n\n- The build passed.\n- `make test` ran.',
        checklist: ['Names `make test` and both facts'],
      },
    });

    await startVoting();

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

  test('labels the pair category above the instruction, outside the prompt band', async () => {
    stubApi();

    await startVoting();

    const trigger = screen.getByRole('button', { name: 'Message' });
    const band = screen.getByRole('button', { name: 'Prompt' }).parentElement;
    expect(band?.contains(trigger)).toBe(false);
    expect(trigger.nextElementSibling?.contains(screen.getByText('Instruction 1'))).toBe(true);
    expect(screen.queryByText('message')).toBeNull();
  });

  test('opens the next pair at the top of the page in fresh output panes', async () => {
    stubApi();
    const user = await startVoting();
    scrollTo.mockClear();
    const firstPane = pane('A');

    await user.keyboard('a{Enter}');
    await screen.findByText('Output A2');

    expect(scrollTo).toHaveBeenCalledWith({ top: 0 });
    expect(pane('A')).not.toBe(firstPane);
  });

  test.each([
    ['A', '12345', ['1', '2', '3', '4', '5', null, null]],
    ['B', '67890', ['6', '7', '8', '9', '0', null, null]],
  ] as const)(
    'orders and submits all seven output %s issues with digit keys on the first five',
    async (side, digits, keys) => {
      const api = stubApi();
      const user = await startVoting();

      await user.keyboard(digits);
      await user.click(within(pane(side)).getByRole('button', { name: 'Issues 5' }));
      const popup = screen.getByRole('dialog', { name: `Issues in output ${side}` });
      const boxes = within(popup).getAllByRole('checkbox');

      expect(
        boxes.map((box) => document.querySelector(`label[for="${box.id}"]`)?.textContent),
      ).toEqual([
        'Misses the brief',
        'Wrong facts',
        'Hard to follow',
        'Wrong tone',
        'Language errors',
        'Formulaic',
        'Too long',
      ]);
      expect(boxes.map((box) => box.getAttribute('aria-keyshortcuts'))).toEqual(keys);
      expect(boxes.map((box) => box.ariaChecked)).toEqual([
        'true',
        'true',
        'true',
        'true',
        'true',
        'false',
        'false',
      ]);
      expect(popup.textContent).not.toContain('undefined');
      expect(
        boxes.map(
          (box) => document.getElementById(box.getAttribute('aria-describedby') ?? '')?.textContent,
        ),
      ).toEqual(WRITING_DEFECTS.map((defect) => defectTerms[defect].definition));

      within(popup).getByRole('checkbox', { name: 'Formulaic' }).focus();
      await user.keyboard(' {Tab} ');

      expect(within(popup).getByRole('checkbox', { name: 'Formulaic' }).ariaChecked).toBe('true');
      expect(within(popup).getByRole('checkbox', { name: 'Too long' }).ariaChecked).toBe('true');
      await user.keyboard('{Escape}');
      expect(within(pane(side)).getByRole('button', { name: 'Issues 7' })).toBeDefined();
      screen.getByRole('group', { name: 'Pair 1' }).focus();

      await user.keyboard('a{Enter}');
      await screen.findByText('Output A2');
      await user.keyboard('b{Enter}');
      await screen.findByText('Output A3');

      expect(api.casts.map((cast) => cast.defects)).toEqual([
        { a: side === 'A' ? WRITING_DEFECTS : [], b: side === 'B' ? WRITING_DEFECTS : [] },
        { a: [], b: [] },
      ]);
    },
  );

  test("keys the family's own issues and lists only those in each Issues popover", async () => {
    const api = stubApi({ defects: ['scope', 'testing'] });
    const user = await startVoting();

    await user.keyboard('2');
    await user.keyboard('3');
    await user.keyboard('6');
    await user.click(within(pane('B')).getByRole('button', { name: 'Issues 1' }));
    const popover = screen.getByRole('dialog', { name: 'Issues in output B' });
    const boxes = within(popover).getAllByRole('checkbox');
    expect(
      boxes.map((box) => [
        document.querySelector(`label[for="${box.id}"]`)?.textContent,
        box.getAttribute('aria-keyshortcuts'),
        box.ariaChecked,
      ]),
    ).toEqual([
      ['Misses the brief', '6', 'true'],
      ['Weak tests', '7', 'false'],
    ]);
    await user.keyboard('{Escape}');
    screen.getByRole('group', { name: 'Pair 1' }).focus();
    await user.keyboard('a{Enter}');
    await screen.findByText('Output A2');

    expect(api.casts.map((cast) => cast.defects)).toEqual([{ a: ['testing'], b: ['scope'] }]);
  });

  test('hides the Issues buttons and their keys for a family that lists no issues', async () => {
    const api = stubApi({ defects: [] });
    const user = await startVoting();

    await user.keyboard('1a{Enter}');
    await screen.findByText('Output A2');

    expect(screen.queryByRole('button', { name: /^Issues/ })).toBeNull();
    expect(api.casts.map((cast) => cast.defects)).toEqual([{ a: [], b: [] }]);
  });

  test.each([
    ['shows', 'text', true],
    ['hides', 'diff', false],
  ] as const)('%s the Markdown toggle beside %s outputs', async (_, render, toggle) => {
    stubApi({ render });

    await startVoting();

    expect(screen.queryByRole('button', { name: 'Markdown' }) !== null).toBe(toggle);
  });

  test('leaves keys on focused header controls to those controls', async () => {
    const api = stubApi();
    const user = await startVoting(
      <>
        <a href="#methodology">Methodology</a>
        <button type="button">Theme</button>
        <VoteApp />
      </>,
    );

    await user.keyboard('a');
    screen.getByRole('link', { name: 'Methodology' }).focus();
    await user.keyboard('b1su{Enter}');
    screen.getByRole('button', { name: 'Theme' }).focus();
    await user.keyboard('b1su{Enter}');

    expect(api.casts).toEqual([]);
    expect(api.undone).toEqual([]);
    expect(screen.getByText('Output A1')).toBeDefined();
    expect(screen.getByRole('radio', { name: 'A is better' })).toHaveProperty(
      'ariaChecked',
      'true',
    );
    expect(within(pane('A')).getByRole('button', { name: 'Issues' })).toBeDefined();
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Theme' }));
  });

  test('groups families by aspect, and starts the one picked', async () => {
    const families: FamilyView[] = [
      { family: 'private-review', aspect: 'private-research', display_name: 'Review' },
      { family: 'private-unlisted', aspect: 'unlisted', display_name: 'Unlisted review' },
      { family: 'private-spaced', aspect: 'private research', display_name: 'Spaced review' },
      { family: 'smoke-text', aspect: null, display_name: 'Smoke' },
      { family: 'coding-repo', aspect: 'coding', display_name: 'Repository' },
      FAMILY,
      { family: 'private-draft', aspect: 'writing-lab', display_name: 'Draft' },
      { family: 'writing-en', aspect: 'writing', display_name: 'English' },
      { family: 'private-edit', aspect: 'private-research', display_name: 'Edit' },
      { family: 'prototype', aspect: '__proto__', display_name: 'Prototype' },
    ];
    const api = stubApi();
    override(api, '/api/families', () => Promise.resolve(Response.json({ families })));
    const errors = vi.spyOn(console, 'error');
    const user = userEvent.setup();
    const expectedGroups: [string, string[]][] = [
      ['Writing', ['Chinese', 'English']],
      ['Coding', ['Repository']],
      ['private-research', ['Review', 'Edit']],
      ['unlisted', ['Unlisted review']],
      ['private research', ['Spaced review']],
      ['writing-lab', ['Draft']],
      ['__proto__', ['Prototype']],
      ['Unlisted', ['Smoke']],
    ];

    render(<VoteApp />);
    const groups = await screen.findAllByRole('region');

    expect(
      groups.map((group) => [
        within(group).getByRole('heading').textContent,
        within(group)
          .getAllByRole('button')
          .map((button) => button.textContent),
      ]),
    ).toEqual(expectedGroups);
    expect(groups).toEqual(expectedGroups.map(([name]) => screen.getByRole('region', { name })));

    await user.click(screen.getByRole('button', { name: FAMILY.display_name }));
    await screen.findByText('Output A1');

    expect(api.pairRequests).toEqual([FAMILY.family]);
    expect(errors).not.toHaveBeenCalled();
  });

  test.each([
    [
      'the family has no round',
      () => Promise.resolve(Response.json({ error: 'no_round' }, { status: 404 })),
      'This family has no outputs available to vote on.',
    ],
    [
      'no server answers',
      () => Promise.reject(new TypeError('Failed to fetch')),
      'The voting service is unavailable. Try again.',
    ],
    [
      'the response is not a pair state',
      () => Promise.resolve(Response.json({ pair: 'private' })),
      'The voting service is unavailable. Try again.',
    ],
  ])('shows on the start screen why voting did not start when %s', async (_, start, alert) => {
    vi.stubGlobal('fetch', (path: string) =>
      path === '/api/families' ? Promise.resolve(Response.json({ families: [FAMILY] })) : start(),
    );
    const user = userEvent.setup();
    render(<VoteApp />);

    await user.click(await screen.findByRole('button', { name: FAMILY.display_name }));

    expect((await screen.findByRole('alert')).textContent).toBe(alert);
    expect(screen.getByRole('heading', { name: 'Choose a family to vote on' })).toBeDefined();
    expect(location.search).toBe('');
  });

  test('returns from a linked family without a round to the family list', async () => {
    stubApi();
    history.replaceState(null, '', '/vote/?family=retired');

    render(<VoteApp />);

    expect((await screen.findByRole('alert')).textContent).toBe(
      'This family has no outputs available to vote on.',
    );
    expect(screen.getByRole('button', { name: FAMILY.display_name })).toBeDefined();
    expect(location.search).toBe('');
  });
});
