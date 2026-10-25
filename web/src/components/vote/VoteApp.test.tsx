// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';

import { VoteApp } from '@/components/vote/VoteApp';
import {
  receiptQuery,
  resumeSessionRequest,
  startSessionRequest,
  voteRequest,
  withdrawRequest,
  type FamilyView,
  type SessionView,
} from '@/lib/arena';
import { defectTerms } from '@/lib/glossary';
import type { Defect } from '@/lib/report';
import { VoteFlow } from '@/lib/vote-flow';
import { COMMAND_ID, VOTE_FAMILY as FAMILY, makeSession, requestBody } from '@/test/arena';

const PRIVATE_FAMILIES: FamilyView[] = [
  { name: 'private-review', aspect: 'private-research', display_name: 'Review', defects: [] },
  { name: 'private-unlisted', aspect: 'unlisted', display_name: 'Unlisted review', defects: [] },
  {
    name: 'private-spaced',
    aspect: 'private research',
    display_name: 'Spaced review',
    defects: [],
  },
  { name: 'smoke-text', aspect: null, display_name: 'Smoke', defects: [] },
];

type Cast = ReturnType<typeof voteRequest.parse>;

interface StubOptions {
  cases?: number;
  prior?: number;
  brief?: { instruction: string; checklist: string[] };
  render?: 'text' | 'html' | 'diff';
  defects?: Defect[];
}

function stubApi({
  cases = 40,
  prior = 0,
  brief,
  render = 'text',
  defects = FAMILY.defects,
}: StubOptions = {}) {
  const family = { ...FAMILY, defects };
  const starts: { family: string }[] = [];
  const resumes: string[] = [];
  const casts: Cast[] = [];
  const active = Array.from({ length: prior }, (_, index) => `prior:${index}`);
  const withdrawn = new Set<string>();
  let undos = 0;
  let served = 0;
  let open: string | null = null;

  const votes = () => active.length;

  const pairOf = (id: string) => ({
    id,
    capability: `capability:${id}`,
    instruction: `Instruction ${id}`,
    checklist: [],
    ...brief,
    ...(render === 'html'
      ? {
          a: { render, url: `https://render.example/render/${id}-a` },
          b: { render, url: `https://render.example/render/${id}-b` },
        }
      : {
          a: { render, text: `Output A${id.slice(1)}` },
          b: { render, text: `Output B${id.slice(1)}` },
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
      family,
      votes: count,
      remaining: cases - count,
      undo_acknowledgment: active.at(-1) ?? null,
      pair: open === null ? null : pairOf(open),
    };
  };

  const draw = (): SessionView => {
    open = `t${++served}`;
    return view();
  };

  const respond = (value: unknown, status = 200) =>
    Promise.resolve(new Response(status === 204 ? null : JSON.stringify(value), { status }));

  vi.stubGlobal('fetch', (path: string, init?: RequestInit) => {
    if (path === '/api/status') {
      return respond({ status: 'ready' });
    }
    if (path === '/api/families') {
      return respond([family]);
    }
    if (path === '/api/sessions') {
      const { family } = startSessionRequest.parse(requestBody(init));
      starts.push({ family });
      return family === FAMILY.name ? respond(draw(), 201) : respond({ error: 'not_found' }, 404);
    }
    if (path === '/api/sessions/resume') {
      const { session_id } = resumeSessionRequest.parse(requestBody(init));
      resumes.push(session_id);
      return session_id === 's1' ? respond(view()) : respond({ error: 'not_found' }, 404);
    }
    if (path === '/api/sessions/withdraw') {
      const request = withdrawRequest.parse(requestBody(init));
      if (withdrawn.has(request.command_id)) {
        return respond(null, 204);
      }
      if (request.acknowledgment !== active.at(-1)) {
        return respond({ error: 'conflict' }, 409);
      }
      withdrawn.add(request.command_id);
      active.pop();
      undos += 1;
      open = null;
      return respond(null, 204);
    }
    if (path === '/api/votes') {
      const request = voteRequest.parse(requestBody(init));
      if (request.lease_id !== open) {
        return respond({ error: 'not_found' }, 404);
      }
      casts.push(request);
      const acknowledgment = `ack:${casts.length}`;
      active.push(acknowledgment);
      open = null;
      return respond({ acknowledgment, session_id: 's1' });
    }
    if (path === '/api/votes/resolve') {
      const request = receiptQuery.parse(requestBody(init));
      const index = casts.findIndex((cast) => cast.command_id === request.command_id);
      return index < 0
        ? respond({ error: 'not_found' }, 404)
        : respond({ acknowledgment: `ack:${index + 1}`, session_id: 's1' });
    }
    throw new Error(`Unexpected API path: ${path}`);
  });
  return {
    starts,
    resumes,
    casts,
    undos: () => undos,
    // Votes the open pair in another tab, which retires it for this one.
    elsewhere: () => {
      if (open === null) {
        throw new Error('Expected a pending lease');
      }
      casts.push({
        command_id: COMMAND_ID,
        lease_id: open,
        capability: `capability:${open}`,
        choice: 'tie',
        defects: { a: [], b: [] },
      });
      active.push(`ack:${casts.length}`);
      open = null;
      draw();
    },
  };
}

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
    sessionStorage.clear();
    document.head.innerHTML = '<meta name="bench-render-origin" content="https://render.example">';
    history.replaceState(null, '', '/vote/');
    vi.spyOn(window, 'scrollTo').mockImplementation(() => undefined);
  });

  afterEach(() => {
    vi.useRealTimers();
    cleanup();
  });

  test('keeps acceptance visible when acquiring the next pair fails', async () => {
    const api = stubApi({ prior: 1 });
    const user = await startSession();
    const original = globalThis.fetch;
    let available = false;
    vi.stubGlobal('fetch', (path: string, init?: RequestInit) => {
      return path === '/api/sessions/resume' && !available
        ? Promise.resolve(Response.json({ error: 'unavailable' }, { status: 503 }))
        : original(path, init);
    });

    await user.keyboard('a{Enter}');
    await screen.findByText('Vote accepted.');

    expect(api.casts.map((cast) => cast.choice)).toEqual(['a']);
    expect(screen.getByRole('button', { name: /Submit vote/ })).toHaveProperty('disabled', true);
    expect(screen.getByRole('button', { name: 'Undo' })).toHaveProperty('disabled', true);
    await user.click(screen.getByRole('button', { name: 'Undo' }));
    expect(api.undos()).toBe(0);
    expect(screen.queryByRole('textbox')).toBeNull();

    available = true;
    await user.click(screen.getByRole('button', { name: 'Retry connection' }));
    await screen.findByText('Output A2');

    expect(api.casts.map((cast) => cast.choice)).toEqual(['a']);
    expect(statusLine()).toBe('Writing · Chinese, 2 votes');
  });

  test('locks voting while an accepted withdrawal awaits session recovery', async () => {
    const api = stubApi({ prior: 1 });
    const user = await startSession();
    const original = globalThis.fetch;
    let available = false;
    vi.stubGlobal('fetch', (path: string, init?: RequestInit) => {
      return path === '/api/sessions/resume' && !available
        ? Promise.resolve(Response.json({ error: 'unavailable' }, { status: 503 }))
        : original(path, init);
    });

    await user.click(screen.getByRole('button', { name: 'Undo' }));
    await screen.findByRole('alert');

    expect(api.undos()).toBe(1);
    expect(screen.getByRole('radio', { name: 'A is better' })).toHaveProperty('disabled', true);
    expect(screen.getByRole('button', { name: 'Undo' })).toHaveProperty('disabled', true);
    await user.keyboard('a{Enter}');
    expect(api.casts).toEqual([]);

    available = true;
    await user.click(screen.getByRole('button', { name: 'Retry connection' }));
    await screen.findByText('Output A2');

    expect(api.undos()).toBe(1);
    expect(api.casts).toEqual([]);
    expect(statusLine()).toBe('Writing · Chinese, 0 votes');
  });

  test('keeps the prior family acknowledgment separate from the requested session', async () => {
    const prior = makeSession();
    const flow = new VoteFlow();
    flow.show(prior);
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Connection lost')));
    await expect(flow.submit(prior, 'a', { a: [], b: [] })).rejects.toThrow();
    const family = { ...FAMILY, name: 'other', display_name: 'Other' };
    const other = makeSession({ id: 's2', family });
    const starts: unknown[] = [];
    let available = false;
    vi.stubGlobal('fetch', (path: string, init?: RequestInit) => {
      if (path === '/api/status') {
        return Promise.resolve(Response.json({ status: 'ready' }));
      }
      if (path === '/api/families') {
        return Promise.resolve(Response.json([FAMILY, family]));
      }
      if (path === '/api/votes/resolve') {
        return Promise.resolve(Response.json({ acknowledgment: 'ack-1', session_id: 's1' }));
      }
      starts.push(requestBody(init));
      return Promise.resolve(
        available ? Response.json(other) : Response.json({ error: 'unavailable' }, { status: 503 }),
      );
    });
    history.replaceState(null, '', '/vote/?family=other');
    const user = userEvent.setup();

    render(<VoteApp />);
    await screen.findByText('Vote accepted.');
    available = true;
    await user.click(screen.getByRole('button', { name: 'Retry connection' }));
    await screen.findByRole('button', { name: '0 votes' });

    expect(starts[1]).toEqual(starts[0]);
    expect(statusLine()).toBe('Writing · Other, 0 votes');
    expect(screen.getByRole('button', { name: 'Undo' })).toHaveProperty('disabled', true);
    expect(screen.queryByText('Saved')).toBeNull();
    expect(screen.queryByText('Vote accepted.')).toBeNull();
  });

  test('explains an empty public family list without owner commands', async () => {
    vi.stubGlobal('fetch', (path: string) =>
      Promise.resolve(Response.json(path === '/api/status' ? { status: 'ready' } : [])),
    );

    render(<VoteApp />);

    await screen.findByText(/No .* (?:available|vote on)/);
    expect(document.body.textContent).not.toContain('uv run');
    expect(screen.getByRole('heading', { name: 'Start a voting session' })).toBeDefined();
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
    await screen.findByText('Output A2');

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

  test('leaves Enter and Space on an issue checkbox to the checkbox', async () => {
    const api = stubApi();
    const user = await startSession();
    await user.keyboard('a');
    const outputA = screen.getByRole('region', { name: 'Output A' });
    await user.click(within(outputA).getByRole('button', { name: 'Issues' }));
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
      const user = await startSession();
      await user.keyboard('a');
      const control =
        name === 'Issues'
          ? within(screen.getByRole('region', { name: 'Output A' })).getByRole('button', {
              name,
            })
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
    const api = stubApi({ prior: 1 });
    await startSession();
    const pair = screen.getByRole('group', { name: /^Pair / });

    for (const key of ['a', '1', 'n', 'u']) {
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
    expect(
      within(screen.getByRole('region', { name: 'Output A' })).getByRole('button', {
        name: 'Issues',
      }),
    ).toBeDefined();
    expect(document.activeElement).toBe(pair);
    expect(api.undos()).toBe(0);
    expect(api.casts).toEqual([]);
  });

  test('keeps the submitted ballot unchanged while its request is pending', async () => {
    const api = stubApi({ prior: 1 });
    const user = await startSession();
    const fetch = globalThis.fetch;
    const pending = Promise.withResolvers<undefined>();
    vi.stubGlobal('fetch', async (path: string, init?: RequestInit) => {
      if (path === '/api/votes') {
        await pending.promise;
      }
      return fetch(path, init);
    });
    await user.keyboard('a');

    await user.click(screen.getByRole('button', { name: /Submit vote/ }));
    const pair = screen.getByRole('group', { name: /^Pair / });
    pair.focus();
    await user.keyboard('b1nu{Enter}');

    expect(screen.getByRole('radio', { name: 'A is better' })).toHaveProperty(
      'ariaChecked',
      'true',
    );
    expect(document.activeElement).toBe(pair);
    expect(api.undos()).toBe(0);

    pending.resolve(undefined);
    await screen.findByText('Output A2');

    expect(api.casts).toMatchObject([{ lease_id: 't1', choice: 'a', defects: { a: [], b: [] } }]);
  });

  test('lays out page outputs at the viewport width picked', async () => {
    vi.stubGlobal(
      'ResizeObserver',
      class {
        observe() {}
        disconnect() {}
      },
    );
    stubApi({ render: 'html' });
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

  test('reports an unavailable family safely and removes its retired session locator', async () => {
    history.replaceState(null, '', '/vote/?family=unknown');
    localStorage.setItem('bench-session:unknown', 'retired');
    const api = stubApi();

    render(<VoteApp />);

    expect((await screen.findByRole('alert')).textContent).toBe(
      'That item is no longer available.',
    );
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
    expect(api.resumes).toEqual(['s1', 's1', 's1']);
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
    screen.getByRole('group', { name: 'Pair 1' }).focus();

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

  test.each([
    ['A', '12345', ['1', '2', '3', '4', '5', null, null]],
    ['B', '67890', ['6', '7', '8', '9', '0', null, null]],
  ] as const)(
    'orders and submits all seven output %s issues with digit keys on the first five',
    async (side, digits, keys) => {
      const api = stubApi();
      const user = await startSession();

      await user.keyboard(digits);
      const pane = screen.getByRole('region', { name: `Output ${side}` });

      await user.click(within(pane).getByRole('button', { name: 'Issues 5' }));
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
      ).toEqual(FAMILY.defects.map((defect) => defectTerms[defect].definition));

      within(popup).getByRole('checkbox', { name: 'Formulaic' }).focus();
      await user.keyboard(' {Tab} ');

      expect(within(popup).getByRole('checkbox', { name: 'Formulaic' }).ariaChecked).toBe('true');
      expect(within(popup).getByRole('checkbox', { name: 'Too long' }).ariaChecked).toBe('true');
      await user.keyboard('{Escape}');
      expect(within(pane).getByRole('button', { name: 'Issues 7' })).toBeDefined();
      screen.getByRole('group', { name: 'Pair 1' }).focus();

      await user.keyboard('a{Enter}');
      await screen.findByText('Output A2');
      await user.keyboard('b{Enter}');
      await screen.findByText('Output A3');

      expect(api.casts.map((cast) => cast.defects)).toEqual([
        {
          a: side === 'A' ? FAMILY.defects : [],
          b: side === 'B' ? FAMILY.defects : [],
        },
        { a: [], b: [] },
      ]);
    },
  );

  test("keys the family's own issues and lists only those in each Issues popover", async () => {
    const api = stubApi({ defects: ['scope', 'testing'] });
    const user = await startSession();

    await user.keyboard('2');
    await user.keyboard('3');
    await user.keyboard('6');
    const outputB = screen.getByRole('region', { name: 'Output B' });
    await user.click(within(outputB).getByRole('button', { name: 'Issues 1' }));
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
    const user = await startSession();

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

    await startSession();

    expect(screen.queryByRole('button', { name: 'Markdown' }) !== null).toBe(toggle);
  });

  test('leaves keys on focused header controls to those controls', async () => {
    const api = stubApi({ prior: 1 });
    const user = await startSession(
      <>
        <a href="#methodology">Methodology</a>
        <button type="button">Theme</button>
        <VoteApp />
      </>,
    );

    await user.keyboard('a');
    screen.getByRole('link', { name: 'Methodology' }).focus();
    await user.keyboard('b1nu{Enter}');
    screen.getByRole('button', { name: 'Theme' }).focus();
    await user.keyboard('b1nu{Enter}');

    expect(api.casts).toEqual([]);
    expect(api.undos()).toBe(0);
    expect(screen.getByText('Output A1')).toBeDefined();
    expect(screen.getByRole('radio', { name: 'A is better' })).toHaveProperty(
      'ariaChecked',
      'true',
    );
    expect(
      within(screen.getByRole('region', { name: 'Output A' })).getByRole('button', {
        name: 'Issues',
      }),
    ).toBeDefined();
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Theme' }));
  });

  test.each(PRIVATE_FAMILIES)('groups aspects and starts $display_name', async (privateFamily) => {
    const families: FamilyView[] = [
      ...PRIVATE_FAMILIES,
      { name: 'coding-repo', aspect: 'coding', display_name: 'Repository', defects: [] },
      FAMILY,
      { name: 'private-draft', aspect: 'writing-lab', display_name: 'Draft', defects: [] },
      { name: 'writing-en', aspect: 'writing', display_name: 'English', defects: [] },
      { name: 'private-edit', aspect: 'private-research', display_name: 'Edit', defects: [] },
      { name: 'prototype', aspect: '__proto__', display_name: 'Prototype', defects: [] },
    ];
    const session: SessionView = {
      id: 'private-session',
      family: privateFamily,
      votes: 0,
      remaining: 1,
      undo_acknowledgment: null,
      pair: {
        id: 'private-pair',
        capability: 'private-pair-capability',
        instruction: 'Compare these private answers.',
        checklist: [],
        a: { render: 'text', text: 'Private answer A' },
        b: { render: 'text', text: 'Private answer B' },
      },
    };
    const fetch = vi.fn((path: string, init?: RequestInit) => {
      if (path === '/api/status') {
        return Promise.resolve(Response.json({ status: 'ready' }));
      }
      if (path === '/api/sessions') {
        startSessionRequest.parse(requestBody(init));
      }
      return Promise.resolve(Response.json(path === '/api/families' ? families : session));
    });
    vi.stubGlobal('fetch', fetch);
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

    await user.click(screen.getByRole('button', { name: privateFamily.display_name }));
    await screen.findByRole('radio', { name: 'A is better' });

    expect(screen.getByRole('region', { name: 'Output A' }).textContent).toContain(
      'Private answer A',
    );
    const start = fetch.mock.calls.find(([path]) => path === '/api/sessions');
    expect(startSessionRequest.parse(requestBody(start?.[1]))).toMatchObject({
      family: privateFamily.name,
    });
    const title =
      privateFamily.aspect === null
        ? privateFamily.display_name
        : `${privateFamily.aspect} · ${privateFamily.display_name}`;
    expect(statusLine()).toBe(`${title}, 0 votes`);
    expect(errors).not.toHaveBeenCalled();
  });

  test.each([
    [
      'the server refuses it',
      () => Promise.resolve(new Response(JSON.stringify({ error: 'conflict' }), { status: 409 })),
      'This pair changed. Review the current outputs and choose again.',
    ],
    [
      'the family is gone',
      () => Promise.resolve(new Response(JSON.stringify({ error: 'not_found' }), { status: 404 })),
      'This family has no outputs available to vote on.',
    ],
    [
      'no server answers',
      () => Promise.reject(new TypeError('Failed to fetch')),
      'The voting service is unavailable. Try again.',
    ],
    [
      'the response body cannot be read',
      async () => {
        const response = new Response(JSON.stringify({ id: 's1' }));
        await response.text();
        return response;
      },
      'The voting service is unavailable. Try again.',
    ],
  ])('shows on the start screen why a session did not start when %s', async (_, start, alert) => {
    vi.stubGlobal('fetch', (path: string) => {
      if (path === '/api/status') {
        return Promise.resolve(Response.json({ status: 'ready' }));
      }
      return path === '/api/families' ? Promise.resolve(Response.json([FAMILY])) : start();
    });
    const user = userEvent.setup();
    render(<VoteApp />);

    await user.click(await screen.findByRole('button', { name: FAMILY.display_name }));

    expect((await screen.findByRole('alert')).textContent).toBe(alert);
  });
});
