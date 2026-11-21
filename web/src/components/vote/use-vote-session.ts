import { useEffect, useEffectEvent, useRef, useState } from 'react';

import type { FamilyView, TaggedDefects, VoteRequest } from '@/lib/arena';
import type { Defect } from '@/lib/defects';
import {
  ApiError,
  castVote,
  confirmVote,
  emptyDraft,
  errorMessage,
  errorMessages,
  getPair,
  isTransientTransport,
  listFamilies,
  NetworkError,
  readDraft,
  skipPair,
  undoVote,
  writeDraft,
  type Draft,
  type PairSnapshot,
} from '@/lib/vote-flow';

const FAMILY_PARAM = 'family';
const SAVED_MS = 1500;
const DROPPED = 'The pair you were choosing on changed, so your choice was cleared.';
const COOKIES = 'Voting needs cookies. Allow cookies for this site, then reload the page.';
const NO_OTHER_PAIR = 'No other pair is available for these outputs.';

const isCode = (reason: unknown, code: ApiError['code']): reason is ApiError =>
  reason instanceof ApiError && reason.code === code;

/** Whether the request may have taken effect. */
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
  const [state, setState] = useState<PairSnapshot | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [sitekey, setSitekey] = useState<string | null>(null);
  const [failedVote, setFailedVote] = useState<VoteRequest | null>(null);
  const [error, setError] = useState<{ message: string; reload: boolean } | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [nextPairFailure, setNextPairFailure] = useState<'retry' | 'no_round' | null>(null);
  const [busy, setBusy] = useState(false);
  // A browser that keeps no cookie asks for admission again right after passing it.
  const admitted = useRef(false);
  const pairRead = useRef<AbortController | null>(null);

  const pair = state?.pair ?? null;
  const locked = busy || sitekey !== null || failedVote !== null;
  const choiceBody =
    pair !== null && draft !== null && draft.choice !== null
      ? { pair: pair.id, choice: draft.choice, defects: draft.defects }
      : null;

  /** Shows a pair state, restoring the tab's draft only when it belongs to the same pair. */
  const show = (name: string, next: PairSnapshot, options: { restore?: boolean } = {}) => {
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
    setFailedVote((current) => (current?.pair === id ? current : null));
    setNextPairFailure(null);
    setError(null);
  };

  const failed = (reason: unknown) => {
    if (isCode(reason, 'admission_required') && admitted.current) {
      setError({ message: COOKIES, reload: false });
    } else if (isCode(reason, 'admission_required') && reason.sitekey !== undefined) {
      setSitekey(reason.sitekey);
      setError(null);
    } else {
      setError({ message: errorMessage(reason), reload: isCode(reason, 'reload_required') });
    }
  };

  /** Returns to the family list with the reason a pair did not load, unless admission can help. */
  const loadFailed = (reason: unknown) => {
    if (isCode(reason, 'admission_required') || isCode(reason, 'reload_required')) {
      failed(reason);
      return;
    }
    history.replaceState(null, '', window.location.pathname);
    setFamily(undefined);
    setState(null);
    setDraft(null);
    setNotice(null);
    setError({ message: errorMessage(reason), reload: false });
  };

  const readPair = async (
    name: string,
    onRead: (next: PairSnapshot) => void,
    onError: (reason: unknown) => void,
  ) => {
    pairRead.current?.abort();
    const controller = new AbortController();
    pairRead.current = controller;
    const { signal } = controller;

    try {
      const next = await getPair(name, signal);
      if (!signal.aborted) {
        onRead(next);
      }
    } catch (reason) {
      if (!signal.aborted) {
        onError(reason);
      }
    } finally {
      if (pairRead.current === controller) {
        pairRead.current = null;
      }
    }
  };

  const load = (
    name: string,
    options: { restore?: boolean; notice?: string; afterVote?: boolean } = {},
  ) =>
    readPair(
      name,
      (next) => {
        show(name, next, options);
        if (options.notice !== undefined) {
          setSaved(false);
          setNotice(options.notice);
        }
      },
      (reason) => {
        if (!options.afterVote) {
          loadFailed(reason);
          return;
        }
        setState(null);
        setDraft(null);
        setNextPairFailure(isCode(reason, 'no_round') ? 'no_round' : 'retry');
        if (reason instanceof ApiError && reason.code !== 'unavailable') {
          failed(reason);
        } else {
          setError({ message: 'Could not load the next pair.', reload: false });
        }
      },
    );

  const act = async (action: () => Promise<void>) => {
    setBusy(true);
    try {
      await action();
    } finally {
      setBusy(false);
    }
  };

  /** Reloads the family if the pending pair or Undo target changed. */
  const perform = (
    name: string,
    action: () => Promise<void>,
    stale: 'stale_pair' | 'nothing_to_undo',
  ) => {
    void act(async () => {
      try {
        await action();
      } catch (reason) {
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
        setError({
          message: `Could not list families: ${errorMessage(reason)}`,
          reload: isCode(reason, 'reload_required'),
        });
      },
    );
    if (family !== undefined) {
      void load(family, { restore: true });
    }
  });

  useEffect(() => {
    initialize();
    return () => {
      pairRead.current?.abort();
    };
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
    history.replaceState(null, '', `?${new URLSearchParams({ [FAMILY_PARAM]: name }).toString()}`);
    setFamily(name);
    void act(() => load(name, { restore: true }));
  };

  /** Reloads the family's pair with the tab's draft, as after a failure or an admission. */
  const reconnect = () => {
    if (family !== undefined) {
      void act(() => load(family, { restore: true, afterVote: nextPairFailure !== null }));
    }
  };

  const verified = () => {
    admitted.current = true;
    setSitekey(null);
    reconnect();
  };

  const holdVote = (body: VoteRequest, reason?: unknown) => {
    setFailedVote(body);
    if (
      reason instanceof ApiError &&
      reason.code !== 'unavailable' &&
      reason.code !== 'not_found'
    ) {
      failed(reason);
    } else {
      setError({ message: 'Could not confirm whether your vote was saved.', reload: false });
    }
  };

  const clearBallot = () => {
    update({ pair: null, choice: null, defects: { a: [], b: [] } });
  };

  const confirmSubmittedVote = async (name: string, body: VoteRequest): Promise<boolean> => {
    try {
      await confirmVote(body);
      return true;
    } catch (reason) {
      if (!isCode(reason, 'stale_pair')) {
        holdVote(body, reason);
        return false;
      }
    }

    await readPair(
      name,
      (next) => {
        const samePair = next.pair?.id === body.pair;
        show(name, next, { restore: samePair });
        if (samePair) {
          holdVote(body);
        } else {
          setNotice(errorMessages.stale_pair);
        }
      },
      (reason) => {
        if (isCode(reason, 'no_round')) {
          clearBallot();
          setFailedVote(null);
          loadFailed(reason);
        } else {
          holdVote(body, reason);
        }
      },
    );
    return false;
  };

  /** Resends the vote whose outcome is unknown, or else sends the draft's choice. */
  const submit = () => {
    const body = failedVote ?? choiceBody;
    if (body === null || family === undefined || busy || sitekey !== null || error?.reload) {
      return;
    }

    void act(async () => {
      setSaved(false);
      setNotice(null);
      try {
        await castVote(body);
      } catch (reason) {
        if (failedVote === null && isTransientTransport(reason)) {
          if (!(await confirmSubmittedVote(family, body))) {
            return;
          }
        } else {
          if (isCode(reason, 'stale_pair')) {
            setFailedVote(null);
            await load(family, { notice: errorMessages.stale_pair });
          } else if (failedVote !== null || unsettled(reason)) {
            holdVote(body, reason);
          } else {
            failed(reason);
          }
          return;
        }
      }

      clearBallot();
      setFailedVote(null);
      setSaved(true);
      await load(family, { afterVote: true });
    });
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
          setState(next);
          setNotice(NO_OTHER_PAIR);
        } else {
          show(family, next);
          setNotice(null);
        }
      },
      'stale_pair',
    );
  };

  const undo = () => {
    const undoPair = state?.undoPair;
    if (!undoPair || family === undefined || locked) {
      return;
    }

    perform(
      family,
      async () => {
        await undoVote({ family, pair: undoPair });
        await load(family, { notice: 'Vote withdrawn.' });
      },
      'nothing_to_undo',
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
    error: error?.message ?? null,
    reloadRequired: error?.reload ?? false,
    notice,
    saved,
    nextPairFailure,
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
