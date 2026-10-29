import { cn } from 'cn';
import { Check, ChevronDown, CircleCheck } from 'lucide-react';
import {
  useEffect,
  useEffectEvent,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react';

import { AspectTitle } from '@/components/site/Method';
import { bleed, pageTitle, sectionTitle, wideContainer } from '@/components/site/PageParts';
import { Hint } from '@/components/site/Term';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { Kbd } from '@/components/ui/kbd';
import { Toggle } from '@/components/ui/toggle';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { DefectPicker } from '@/components/vote/DefectPicker';
import { InlineMarkdown, Markdown, prose } from '@/components/vote/Markdown';
import { OutputPane } from '@/components/vote/OutputPane';
import { band, panel, scroll } from '@/components/vote/panel';
import { Verification } from '@/components/vote/Verification';
import type {
  Choice,
  FamilyView,
  PairState,
  PairView,
  TaggedDefects,
  VoteRequest,
} from '@/lib/arena';
import { familyTitle, groupByAspect, isAspect } from '@/lib/aspects';
import type { Defect } from '@/lib/defects';
import { formatCountOf } from '@/lib/format';
import { categoryTerm } from '@/lib/glossary';
import {
  ApiError,
  castVote,
  emptyDraft,
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
const STALE_PAIR = 'This pair changed. Review the current outputs and choose again.';
const DROPPED = 'The pair you were choosing on changed, so your unsent choice was cleared.';
const NO_OTHER_PAIR = 'No other pair is available for these outputs.';

const CHOICES: { value: Choice; label: string; key: string; className?: string }[] = [
  { value: 'a', label: 'A is better', key: 'a' },
  { value: 'tie', label: 'Tie', key: 't', className: 'max-sm:order-1' },
  { value: 'both_bad', label: 'Both bad', key: 'x', className: 'max-sm:order-1' },
  { value: 'b', label: 'B is better', key: 'b' },
];

const SIDES: { side: 'A' | 'B'; key: keyof TaggedDefects }[] = [
  { side: 'A', key: 'a' },
  { side: 'B', key: 'b' },
];

const DEFECT_KEYS: Record<keyof TaggedDefects, readonly string[]> = {
  a: ['1', '2', '3', '4', '5'],
  b: ['6', '7', '8', '9', '0'],
};

function keyedDefects(defects: readonly Defect[]) {
  return new Map(
    SIDES.flatMap(({ key }) =>
      DEFECT_KEYS[key].flatMap((shortcut, index) => {
        const defect = defects.at(index);

        return defect === undefined ? [] : [[shortcut, { key, defect }] as const];
      }),
    ),
  );
}

function message(error: unknown): string {
  return error instanceof ApiError || error instanceof NetworkError
    ? error.message
    : 'The voting service is unavailable. Try again.';
}

const isCode = (reason: unknown, code: ApiError['code']): reason is ApiError =>
  reason instanceof ApiError && reason.code === code;

function ErrorAlert({ error }: { error: ReactNode }) {
  return (
    error !== null && (
      <p role="alert" className="text-sm text-destructive">
        {error}
      </p>
    )
  );
}

function Notice({ notice }: { notice: string | null }) {
  return (
    notice !== null && (
      <p role="status" className="text-sm text-muted-foreground">
        {notice}
      </p>
    )
  );
}

function Screen({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className={cn(wideContainer, 'flex flex-col gap-4 pt-8 pb-16')}>
      <h1 className={pageTitle}>{title}</h1>
      {children}
    </div>
  );
}

function StartScreen({
  families,
  busy,
  error,
  onStart,
}: {
  families: FamilyView[] | null;
  busy: boolean;
  error: ReactNode;
  onStart: (family: string) => void;
}) {
  const groupsId = useId();

  return (
    <Screen title="Choose a family to vote on">
      <ErrorAlert error={error} />
      {families?.length === 0 && (
        <p className="text-muted-foreground">
          No voting families are available right now. Please check back later.
        </p>
      )}
      {families &&
        groupByAspect(families).map(({ aspect, members }, index) => {
          const id = `${groupsId}-${index}`;

          return (
            <section
              key={JSON.stringify(aspect)}
              className="flex flex-col gap-3"
              aria-labelledby={id}
            >
              <h2 id={id} className={cn(sectionTitle, 'flex items-center gap-2')}>
                {aspect !== null && isAspect(aspect) ? (
                  <AspectTitle aspect={aspect} />
                ) : (
                  (aspect ?? 'Unlisted')
                )}
              </h2>
              <ul className="flex flex-wrap gap-2">
                {members.map((family) => (
                  <li key={family.family}>
                    <Button
                      variant="outline"
                      disabled={busy}
                      onClick={() => {
                        onStart(family.family);
                      }}
                    >
                      {family.display_name}
                    </Button>
                  </li>
                ))}
              </ul>
            </section>
          );
        })}
    </Screen>
  );
}

const checklistHint = {
  term: 'What to check',
  definition: 'Points for you to judge by. The model never saw them.',
};

function Brief({
  pair,
  collapsed,
  onCollapsedChange,
  status,
}: {
  pair: PairView;
  collapsed: boolean;
  onCollapsedChange: (collapsed: boolean) => void;
  status: ReactNode;
}) {
  const checklistTitle = useId();
  const stacked = pair.render !== 'text';

  return (
    <Collapsible
      open={!collapsed}
      onOpenChange={(open) => {
        onCollapsedChange(!open);
      }}
      asChild
    >
      <Card
        size="sm"
        className={cn(
          panel,
          'data-[state=closed]:self-start md:col-span-2',
          !stacked && 'xl:col-span-1 xl:data-[state=closed]:row-span-1',
        )}
      >
        <div className={cn(band, 'group-data-[state=open]/card:border-b')}>
          <CollapsibleTrigger asChild>
            <Button variant="ghost" size="sm" className="-ml-2">
              <ChevronDown
                className="transition-transform in-data-[state=closed]:-rotate-90"
                aria-hidden="true"
              />
              Prompt
            </Button>
          </CollapsibleTrigger>
          {status}
        </div>
        <CollapsibleContent asChild>
          <CardContent
            className={cn(
              scroll,
              'flex flex-col gap-3 text-base',
              stacked ? 'md:max-h-[25dvh]' : 'md:max-xl:max-h-[25dvh]',
            )}
          >
            <Hint term={categoryTerm(pair.category)} side="bottom" />
            <Markdown text={pair.instruction} />
            {pair.checklist.length > 0 && (
              <section
                aria-labelledby={checklistTitle}
                className="flex flex-col gap-1 rounded-lg bg-muted p-3 ring-1 ring-foreground/10"
              >
                <h2 id={checklistTitle} className="text-sm font-medium text-muted-foreground">
                  <Hint term={checklistHint} />
                </h2>
                <ul className={cn(prose, 'text-muted-foreground')}>
                  {pair.checklist.map((item) => (
                    <li key={item} className="flex gap-2 ps-0">
                      <CircleCheck className="h-lh w-4 shrink-0" aria-hidden="true" />
                      <InlineMarkdown text={item} />
                    </li>
                  ))}
                </ul>
              </section>
            )}
          </CardContent>
        </CollapsibleContent>
      </Card>
    </Collapsible>
  );
}

/**
 * The vote page. The server keeps the pending pair, and this tab keeps the voter's draft and view
 * in `sessionStorage`, so a reload shows exactly what the voter saw and never sends a vote.
 */
export function VoteApp() {
  // The page renders only in the browser, so its URL is readable on the first render.
  const [family, setFamily] = useState(
    () => new URLSearchParams(window.location.search).get(FAMILY_PARAM) ?? readDraft()?.family,
  );
  const [families, setFamilies] = useState<FamilyView[] | null>(null);
  const [state, setState] = useState<PairState | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [sitekey, setSitekey] = useState<string | null>(null);
  const [failedVote, setFailedVote] = useState<VoteRequest | null>(null);
  const [error, setError] = useState<ReactNode>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(family !== undefined);
  const pairView = useRef<HTMLDivElement>(null);
  const panes = useRef<Record<keyof TaggedDefects, HTMLDivElement | null>>({ a: null, b: null });
  const restoring = useRef<Draft['scroll'] | null>(null);

  const pair = state?.pair ?? null;
  const locked = busy || sitekey !== null || failedVote !== null;
  const title = familyTitle(
    families?.find((item) => item.family === family) ?? {
      aspect: null,
      display_name: family ?? '',
    },
  );

  /** Shows a pair state, restoring the tab's draft only when it belongs to the same pair. */
  const show = (name: string, next: PairState, options: { restore?: boolean } = {}) => {
    history.replaceState(null, '', `?${new URLSearchParams({ [FAMILY_PARAM]: name })}`);
    const stored = readDraft();
    const id = next.pair?.id ?? null;
    let shown: Draft;
    restoring.current = null;
    if (options.restore && stored?.family === name && stored.pair === id) {
      shown = stored;
      restoring.current = stored.scroll;
    } else {
      if (options.restore && stored?.family === name && stored.choice !== null) {
        setNotice(DROPPED);
      }
      shown = {
        ...emptyDraft(name, id),
        markdown: draft?.markdown ?? false,
        collapsed:
          (draft?.collapsed ?? false) && state?.pair?.instruction === next.pair?.instruction,
      };
    }
    writeDraft(shown);
    setDraft(shown);
    setState(next);
    setFamily(name);
    setError(null);
  };

  const failed = (reason: unknown) => {
    if (isCode(reason, 'admission_required') && reason.sitekey !== undefined) {
      setSitekey(reason.sitekey);
      setError(null);
    } else {
      setError(message(reason));
    }
  };

  const load = async (name: string, options: { restore?: boolean; notice?: string } = {}) => {
    try {
      show(name, await getPair(name), options);
      if (options.notice !== undefined) {
        setSaved(false);
        setNotice(options.notice);
      }
    } catch (reason) {
      if (isCode(reason, 'admission_required')) {
        setFamily(name);
      }
      failed(reason);
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

  const initialize = useEffectEvent(() => {
    listFamilies().then(
      ({ families }) => {
        setFamilies(families);
      },
      (reason: unknown) => {
        setError(<>Could not list families: {message(reason)}</>);
      },
    );
    if (family !== undefined) {
      getPair(family)
        .then((next) => {
          show(family, next, { restore: true });
        }, failed)
        .finally(() => {
          setLoading(false);
        });
    }
  });

  useEffect(() => {
    initialize();
  }, []);

  useLayoutEffect(() => {
    pairView.current?.focus({ preventScroll: true });
    const offsets = restoring.current ?? { window: 0, a: 0, b: 0 };
    restoring.current = null;
    window.scrollTo({ top: offsets.window });
    for (const { key } of SIDES) {
      const pane = panes.current[key];
      if (pane !== null) {
        pane.scrollTop = offsets[key];
      }
    }
  }, [pair?.id]);

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

  const onPageHide = useEffectEvent(() => {
    if (draft !== null) {
      writeDraft({
        ...draft,
        scroll: {
          window: window.scrollY,
          a: panes.current.a?.scrollTop ?? 0,
          b: panes.current.b?.scrollTop ?? 0,
        },
      });
    }
  });

  useEffect(() => {
    const listener = () => {
      onPageHide();
    };
    window.addEventListener('pagehide', listener);

    return () => {
      window.removeEventListener('pagehide', listener);
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

  const submit = (body: VoteRequest | null) => {
    if (body === null || family === undefined || busy || sitekey !== null) {
      return;
    }

    void act(async () => {
      try {
        await castVote(body);
        setFailedVote(null);
        setSaved(true);
        setNotice(null);
        await load(family);
      } catch (reason) {
        if (isCode(reason, 'stale_pair')) {
          setFailedVote(null);
          await load(family, { notice: STALE_PAIR });
        } else {
          setFailedVote(body);
          failed(reason);
        }
      }
    });
  };

  const choiceBody =
    pair !== null && draft?.choice !== null && draft !== null
      ? { pair: pair.id, choice: draft.choice, defects: draft.defects }
      : null;

  const skip = () => {
    if (pair === null || family === undefined || locked) {
      return;
    }

    void act(async () => {
      try {
        const next = await skipPair(pair.id);
        if (next.pair?.id === pair.id) {
          setNotice(NO_OTHER_PAIR);
        } else {
          show(family, next);
          setNotice(null);
        }
      } catch (reason) {
        if (isCode(reason, 'stale_pair')) {
          await load(family, { notice: STALE_PAIR });
        } else {
          failed(reason);
        }
      }
    });
  };

  const undo = () => {
    if (state?.undo !== true || family === undefined || locked) {
      return;
    }

    void act(async () => {
      try {
        await undoVote(family);
        await load(family, { notice: 'Vote withdrawn.' });
      } catch (reason) {
        if (isCode(reason, 'nothing_to_undo')) {
          await load(family, { notice: reason.message });
        } else {
          failed(reason);
        }
      }
    });
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

  const onKey = useEffectEvent((event: KeyboardEvent) => {
    if (event.defaultPrevented || event.isComposing || event.repeat || busy) {
      return;
    }

    const target = event.target;
    if (event.metaKey || event.ctrlKey || event.altKey) {
      return;
    }

    if (event.key === 'Enter') {
      if (target instanceof Node && target.contains(pairView.current)) {
        event.preventDefault();
        submit(failedVote ?? choiceBody);
      }
      return;
    }

    if (
      !(target instanceof Element) ||
      !pairView.current?.contains(target) ||
      target.closest('input, select, textarea, [contenteditable], [role="textbox"]') !== null
    ) {
      return;
    }

    const key = event.key.toLowerCase();
    if (locked) {
      return;
    }
    if (key === 'u') {
      event.preventDefault();
      undo();
      return;
    }
    if (pair === null) {
      return;
    }
    if (key === 's') {
      event.preventDefault();
      skip();
      return;
    }

    const picked = CHOICES.find((item) => item.key === key);
    const keyed = keyedDefects(pair.defects).get(event.key);
    if (picked) {
      update({ choice: picked.value });
    } else if (keyed !== undefined) {
      toggleDefect(keyed.key, keyed.defect);
    } else {
      return;
    }

    event.preventDefault();
  });

  useEffect(() => {
    window.addEventListener('keydown', onKey);

    return () => {
      window.removeEventListener('keydown', onKey);
    };
  }, []);

  const recovery =
    sitekey !== null ? (
      <Verification
        sitekey={sitekey}
        onReady={() => {
          setSitekey(null);
          if (family !== undefined) {
            void act(() => load(family, { restore: true }));
          }
        }}
      />
    ) : failedVote !== null ? (
      <Button
        disabled={busy}
        onClick={() => {
          submit(failedVote);
        }}
      >
        Retry vote
      </Button>
    ) : (
      error !== null &&
      family !== undefined && (
        <Button
          disabled={busy}
          onClick={() => {
            void act(() => load(family, { restore: true }));
          }}
        >
          Retry connection
        </Button>
      )
    );

  if (loading) {
    return (
      <Screen title="Connecting to voting">
        <p role="status">Checking your browser...</p>
      </Screen>
    );
  }

  if (state === null) {
    if (family !== undefined && (sitekey !== null || error !== null)) {
      return (
        <Screen title="Connecting to voting">
          <ErrorAlert error={error} />
          <div className="flex flex-col items-start gap-2">{recovery}</div>
        </Screen>
      );
    }

    return (
      <StartScreen
        families={families}
        busy={busy}
        error={error}
        onStart={(name) => {
          void act(() => load(name, { restore: true }));
        }}
      />
    );
  }

  if (pair === null || draft === null) {
    return (
      <div ref={pairView} tabIndex={-1} role="group" aria-label="Completed family">
        <Screen title="No pairs left">
          <p className="text-muted-foreground">
            {formatCountOf(state.votes, 'vote')} recorded on {title}.
          </p>
          <Notice notice={notice} />
          <ErrorAlert error={error} />
          {recovery}
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" className="text-foreground" asChild>
              <a href={window.location.pathname}>Choose another family</a>
            </Button>
            <Button variant="outline" onClick={undo} disabled={!state.undo || locked}>
              Undo last vote
            </Button>
          </div>
        </Screen>
      </div>
    );
  }

  const hasText = pair.render === 'text';
  const status = (
    <div className="ml-auto flex flex-wrap items-center gap-x-4 gap-y-2 text-sm text-muted-foreground">
      <span role="status" className="flex items-center gap-1 empty:sr-only">
        {saved ? (
          <>
            <Check className="size-4" aria-hidden="true" />
            Saved
          </>
        ) : (
          notice
        )}
      </span>
      <span className="whitespace-nowrap tabular-nums">
        {title},{' '}
        <Hint
          term={{
            term: 'Your votes',
            definition: `${formatCountOf(state.remaining, 'pair')} left to vote on.`,
          }}
        >
          {formatCountOf(state.votes, 'vote')}
        </Hint>
      </span>
    </div>
  );

  return (
    <div
      ref={pairView}
      tabIndex={-1}
      role="group"
      aria-label={`Pair ${state.votes + 1}`}
      className={cn(wideContainer, 'flex flex-col gap-3 py-3 outline-none md:min-h-0 md:flex-1')}
    >
      <div
        className={cn(
          'grid gap-3 md:min-h-0 md:flex-1 md:grid-cols-2 md:grid-rows-[auto_minmax(0,1fr)]',
          hasText &&
            'xl:grid-cols-[minmax(0,24rem)_minmax(0,1fr)_minmax(0,1fr)] xl:gap-y-0 xl:*:row-span-2 xl:*:grid xl:*:grid-rows-subgrid',
        )}
      >
        <Brief
          pair={pair}
          collapsed={draft.collapsed}
          onCollapsedChange={(collapsed) => {
            update({ collapsed });
          }}
          status={status}
        />
        {SIDES.map(({ side, key }) => (
          <OutputPane
            key={`${key}-${pair.id}`}
            side={side}
            render={pair.render}
            text={pair[key]}
            asMarkdown={draft.markdown}
            scrollRef={(element) => {
              panes.current[key] = element;
            }}
            controls={
              pair.defects.length > 0 && (
                <DefectPicker
                  side={side}
                  defects={pair.defects}
                  tagged={draft.defects[key]}
                  disabled={locked}
                  keys={DEFECT_KEYS[key]}
                  onToggle={(defect) => {
                    toggleDefect(key, defect);
                  }}
                />
              )
            }
          />
        ))}
      </div>

      <div className="flex flex-col gap-3">
        <ErrorAlert error={error} />
        {hasText && (
          <div className="flex flex-wrap items-center gap-3">
            <Toggle
              variant="outline"
              pressed={draft.markdown}
              onPressedChange={(markdown) => {
                update({ markdown });
              }}
            >
              Markdown
            </Toggle>
          </div>
        )}
      </div>

      <div
        className={cn(
          bleed,
          'grid gap-2 max-md:sticky max-md:bottom-0 max-md:border-t max-md:bg-background max-md:py-3 sm:grid-cols-[1fr_auto]',
        )}
      >
        {(sitekey !== null || failedVote !== null) && (
          <div className="sm:col-span-2">{recovery}</div>
        )}
        <ToggleGroup
          type="single"
          variant="choice"
          size="lg"
          spacing={2}
          value={draft.choice ?? ''}
          disabled={locked}
          onValueChange={(value) => {
            update({ choice: CHOICES.find((item) => item.value === value)?.value ?? null });
          }}
          aria-label="Verdict"
          className="grid w-full grid-cols-2 sm:grid-cols-4"
        >
          {CHOICES.map(({ value, label, key, className }) => (
            <ToggleGroupItem
              key={value}
              value={value}
              aria-keyshortcuts={key.toUpperCase()}
              className={className}
            >
              {label}
              <Kbd
                aria-hidden="true"
                className="in-data-[state=on]:bg-primary-foreground in-data-[state=on]:text-primary"
              >
                {key.toUpperCase()}
              </Kbd>
            </ToggleGroupItem>
          ))}
        </ToggleGroup>

        <div className="flex gap-2">
          <Button
            variant="outline"
            size="lg"
            onClick={undo}
            disabled={!state.undo || locked}
            aria-keyshortcuts="U"
          >
            Undo
            <Kbd aria-hidden="true">U</Kbd>
          </Button>
          <Button
            variant="outline"
            size="lg"
            onClick={skip}
            disabled={locked}
            aria-keyshortcuts="S"
          >
            Skip
            <Kbd aria-hidden="true">S</Kbd>
          </Button>
          <Button
            size="lg"
            onClick={() => {
              submit(choiceBody);
            }}
            disabled={choiceBody === null || locked}
            aria-keyshortcuts="Enter"
            className="max-sm:flex-1"
          >
            Submit vote
            <Kbd aria-hidden="true" className="bg-primary-foreground text-primary">
              ↵
            </Kbd>
          </Button>
        </div>
      </div>
    </div>
  );
}
