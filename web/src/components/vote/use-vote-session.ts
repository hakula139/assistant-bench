import { useEffect, useEffectEvent, useRef, useState } from 'react';

import type { FamilyView, PairState, TaggedDefects, VoteRequest } from '@/lib/arena';
import type { Defect } from '@/lib/defects';
import {
  ApiError,
  castVote,
  emptyDraft,
  errorMessage,
  errorMessages,
  getPair,
  listFamilies,
  NetworkError,
  readDraft,
  skipPair,
  undoVote,
  writeDraft,
  type Draft,
} from '@/lib/vote-flow';

const FAMILY_PARAM = 'family';
const SAVED_MS = 1500;
const DROPPED = 'The pair you were choosing on changed, so your choice was cleared.';
const COOKIES = 'Voting needs cookies. Allow cookies for this site, then reload the page.';
const NO_OTHER_PAIR = 'No other pair is available for these outputs.';

const isCode = (reason: unknown, code: ApiError['code']): reason is ApiError =>
  reason instanceof ApiError && reason.code === code;

/** Whether the request may have taken effect, which only resending it can settle. */
const unsettled = (reason: unknown) =>
  reason instanceof NetworkError || isCode(reason, 'unavailable');

/**
 * The voter's session with the vote API: the chosen family, its pending pair, this tab's draft,
 * and the admission, failure and retry state around them. While `locked`, the ballot stays as
 * the voter left it.
 */
export function useVoteSession() {
  // The page renders only in the browser, so its URL is readable on the first render.
  const [family, setFamily] = useState(
    () => new URLSearchParams(window.location.search).get(FAMILY_PARAM) ?? undefined,
  );
  const [families, setFamilies] = useState<FamilyView[] | null>(null);
  const [state, setState] = useState<PairState | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [sitekey, setSitekey] = useState<string | null>(null);
  const [failedVote, setFailedVote] = useState<VoteRequest | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);
  // A browser that keeps no cookie asks for admission again right after passing it.
  const admitted = useRef(false);

  const pair = state?.pair ?? null;
  const locked = busy || sitekey !== null || failedVote !== null;
  const choiceBody =
    pair !== null && draft !== null && draft.choice !== null
      ? { pair: pair.id, choice: draft.choice, defects: draft.defects }
      : null;

  /** Shows a pair state, restoring the tab's draft only when it belongs to the same pair. */
  const show = (name: string, next: PairState, options: { restore?: boolean } = {}) => {
    const stored = readDraft();
    const id = next.pair?.id ?? null;
    let shown: Draft;
    if (options.restore && stored?.family === name && stored.pair === id) {
      shown = stored;
    } else {
      if (options.restore && stored?.family === name && stored.choice !== null) {
        setNotice(DROPPED);
      }
      shown = {
        ...emptyDraft(name, id),
        markdown: draft?.markdown ?? stored?.markdown ?? false,
        collapsed:
          (draft?.collapsed ?? false) && state?.pair?.instruction === next.pair?.instruction,
      };
    }
    writeDraft(shown);
    setDraft(shown);
    setState(next);
    setError(null);
  };

  const failed = (reason: unknown) => {
    if (isCode(reason, 'admission_required') && admitted.current) {
      setError(COOKIES);
    } else if (isCode(reason, 'admission_required') && reason.sitekey !== undefined) {
      setSitekey(reason.sitekey);
      setError(null);
    } else {
      setError(errorMessage(reason));
    }
  };

  /** Returns to the family list with the reason a pair did not load, unless admission can help. */
  const loadFailed = (reason: unknown) => {
    if (isCode(reason, 'admission_required')) {
      failed(reason);
      return;
    }
    history.replaceState(null, '', window.location.pathname);
    setFamily(undefined);
    setState(null);
    setDraft(null);
    setNotice(null);
    setError(errorMessage(reason));
  };

  const load = async (name: string, options: { restore?: boolean; notice?: string } = {}) => {
    try {
      show(name, await getPair(name), options);
      if (options.notice !== undefined) {
        setSaved(false);
        setNotice(options.notice);
      }
    } catch (reason) {
      loadFailed(reason);
    }
  };

  const act = async (action: () => Promise<void>) => {
    setBusy(true);
    try {
      await action();
    } finally {
      setBusy(false);
    }
  };

  /**
   * Runs a pair action. A refusal with the `stale` code loads the current pair under that code's
   * message, and a `vote` whose outcome is unknown locks the ballot until it is resent. A retry
   * refused by the rate limit leaves the first attempt's outcome unknown.
   */
  const perform = (
    name: string,
    action: () => Promise<void>,
    { stale, vote }: { stale: 'stale_pair' | 'nothing_to_undo'; vote?: VoteRequest },
  ) => {
    void act(async () => {
      try {
        await action();
      } catch (reason) {
        const unknown =
          unsettled(reason) || (vote === failedVote && isCode(reason, 'rate_limited'));
        setFailedVote(vote !== undefined && unknown ? vote : null);
        if (isCode(reason, stale)) {
          await load(name, { notice: errorMessages[stale] });
        } else {
          failed(reason);
        }
      }
    });
  };

  const initialize = useEffectEvent(() => {
    listFamilies().then(
      ({ families }) => {
        setFamilies(families);
      },
      (reason: unknown) => {
        setError(`Could not list families: ${errorMessage(reason)}`);
      },
    );
    if (family !== undefined) {
      getPair(family).then((next) => {
        show(family, next, { restore: true });
      }, loadFailed);
    }
  });

  useEffect(() => {
    initialize();
  }, []);

  useEffect(() => {
    if (!saved) {
      return;
    }

    const timer = setTimeout(() => {
      setSaved(false);
    }, SAVED_MS);

    return () => {
      clearTimeout(timer);
    };
  }, [saved]);

  const update = (change: Partial<Draft>) => {
    setDraft((current) => {
      if (current === null) {
        return current;
      }
      const next = { ...current, ...change };
      writeDraft(next);
      return next;
    });
  };

  const start = (name: string) => {
    history.replaceState(null, '', `?${new URLSearchParams({ [FAMILY_PARAM]: name })}`);
    setFamily(name);
    void act(() => load(name, { restore: true }));
  };

  /** Reloads the family's pair with the tab's draft, as after a failure or an admission. */
  const reconnect = () => {
    if (family !== undefined) {
      void act(() => load(family, { restore: true }));
    }
  };

  const verified = () => {
    admitted.current = true;
    setSitekey(null);
    reconnect();
  };

  /** Resends the vote whose outcome is unknown, or else sends the draft's choice. */
  const submit = () => {
    const body = failedVote ?? choiceBody;
    if (body === null || family === undefined || busy || sitekey !== null) {
      return;
    }

    perform(
      family,
      async () => {
        await castVote(body);
        update({ choice: null });
        setFailedVote(null);
        setSaved(true);
        setNotice(null);
        await load(family);
      },
      { stale: 'stale_pair', vote: body },
    );
  };

  const skip = () => {
    if (pair === null || family === undefined || locked) {
      return;
    }

    perform(
      family,
      async () => {
        const next = await skipPair(pair.id);
        if (next.pair?.id === pair.id) {
          setNotice(NO_OTHER_PAIR);
        } else {
          show(family, next);
          setNotice(null);
        }
      },
      { stale: 'stale_pair' },
    );
  };

  const undo = () => {
    if (state?.undo !== true || family === undefined || locked) {
      return;
    }

    perform(
      family,
      async () => {
        await undoVote(family);
        await load(family, { notice: 'Vote withdrawn.' });
      },
      { stale: 'nothing_to_undo' },
    );
  };

  const toggleDefect = (side: keyof TaggedDefects, defect: Defect) => {
    if (locked || draft === null || pair === null) {
      return;
    }
    update({
      defects: {
        ...draft.defects,
        [side]: pair.defects.filter(
          (item) => (item === defect) !== draft.defects[side].includes(item),
        ),
      },
    });
  };

  return {
    family,
    families,
    state,
    pair,
    draft,
    sitekey,
    voteFailed: failedVote !== null,
    error,
    notice,
    saved,
    busy,
    locked,
    start,
    reconnect,
    verified,
    update,
    submit,
    skip,
    undo,
    toggleDefect,
  };
}
