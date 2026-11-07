import { cn } from 'cn';
import { Check, ChevronDown, CircleCheck } from 'lucide-react';
import {
  useCallback,
  useEffect,
  useEffectEvent,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
  type RefObject,
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
import { useVoteSession } from '@/components/vote/use-vote-session';
import { Verification } from '@/components/vote/Verification';
import type { Choice, FamilyView, PairView, TaggedDefects } from '@/lib/arena';
import { familyTitle, groupByAspect, isAspect } from '@/lib/aspects';
import type { Defect } from '@/lib/defects';
import { formatCountOf } from '@/lib/format';
import { categoryTerms } from '@/lib/glossary';
import { writeDraft } from '@/lib/vote-flow';

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

function ErrorAlert({ error }: { error: string | null }) {
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
  error,
  onStart,
}: {
  families: FamilyView[] | null;
  error: string | null;
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
  scrollRef,
}: {
  pair: PairView;
  collapsed: boolean;
  onCollapsedChange: (collapsed: boolean) => void;
  status: ReactNode;
  scrollRef: RefObject<HTMLDivElement | null>;
}) {
  const checklistTitle = useId();
  const content = useRef<HTMLDivElement>(null);
  const [moreBelow, setMoreBelow] = useState(false);
  const stacked = pair.render !== 'text';
  const measure = useCallback(() => {
    const element = scrollRef.current;
    if (element !== null) {
      setMoreBelow(element.scrollHeight - element.clientHeight - element.scrollTop > 1);
    }
  }, [scrollRef]);

  useLayoutEffect(() => {
    const element = scrollRef.current;
    const contents = content.current;
    if (element === null || contents === null) {
      return;
    }

    const observer = new ResizeObserver(measure);
    observer.observe(element);
    observer.observe(contents);

    return () => {
      observer.disconnect();
    };
  }, [pair.id, collapsed, measure, scrollRef]);

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
          <div
            className={cn(
              'relative flex min-h-0 flex-1 flex-col',
              stacked ? 'md:max-h-[25dvh]' : 'md:max-xl:max-h-[25dvh]',
            )}
          >
            <CardContent
              ref={scrollRef}
              role="region"
              aria-label="Prompt contents"
              tabIndex={0}
              onScroll={measure}
              className={cn(scroll, 'text-base')}
            >
              <div ref={content} className="flex flex-col gap-3">
                <Hint term={categoryTerms[pair.category]} side="bottom" />
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
              </div>
            </CardContent>
            {moreBelow && (
              <div
                aria-hidden="true"
                className="pointer-events-none absolute inset-x-0 bottom-0 flex justify-center bg-gradient-to-t from-card from-50% to-transparent pt-6 pb-2 text-xs text-muted-foreground"
              >
                Scroll for more
              </div>
            )}
          </div>
        </CollapsibleContent>
      </Card>
    </Collapsible>
  );
}

function StatusBand({
  title,
  votes,
  remaining,
  saved,
  notice,
}: {
  title: string;
  votes: number;
  remaining: number;
  saved: boolean;
  notice: string | null;
}) {
  return (
    <div className="ml-auto flex flex-wrap items-center justify-end gap-x-4 gap-y-2 text-sm text-muted-foreground max-sm:basis-full">
      <span role="status" className="flex items-center gap-1 empty:sr-only max-sm:order-last">
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
            definition: `${formatCountOf(remaining, 'pair')} left to vote on.`,
          }}
        >
          {formatCountOf(votes, 'vote')}
        </Hint>
      </span>
    </div>
  );
}

function Ballot({
  choice,
  undoable,
  locked,
  error,
  recovery,
  onChoose,
  onUndo,
  onSkip,
  onSubmit,
}: {
  choice: Choice | null;
  undoable: boolean;
  locked: boolean;
  error: string | null;
  recovery: ReactNode;
  onChoose: (choice: Choice | null) => void;
  onUndo: () => void;
  onSkip: () => void;
  onSubmit: () => void;
}) {
  return (
    <div
      className={cn(
        bleed,
        'grid gap-2 max-md:sticky max-md:bottom-0 max-md:border-t max-md:bg-background max-md:py-3 sm:grid-cols-[1fr_auto]',
      )}
    >
      {(error !== null || recovery !== null) && (
        <div className="flex flex-col items-start gap-2 sm:col-span-2">
          <ErrorAlert error={error} />
          {recovery}
        </div>
      )}
      <ToggleGroup
        type="single"
        variant="choice"
        size="lg"
        spacing={2}
        value={choice ?? ''}
        disabled={locked}
        onValueChange={(value) => {
          onChoose(CHOICES.find((item) => item.value === value)?.value ?? null);
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
          onClick={onUndo}
          disabled={!undoable || locked}
          aria-keyshortcuts="U"
        >
          Undo
          <Kbd aria-hidden="true">U</Kbd>
        </Button>
        <Button
          variant="outline"
          size="lg"
          onClick={onSkip}
          disabled={locked}
          aria-keyshortcuts="S"
        >
          Skip
          <Kbd aria-hidden="true">S</Kbd>
        </Button>
        <Button
          size="lg"
          onClick={onSubmit}
          disabled={choice === null || locked}
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
  );
}

/**
 * The vote page. The server keeps the pending pair, and this tab keeps the voter's draft and view
 * in `sessionStorage`, so a reload shows exactly what the voter saw and never sends a vote.
 */
export function VoteApp() {
  const {
    family,
    families,
    state,
    pair,
    draft,
    sitekey,
    voteFailed,
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
  } = useVoteSession();
  const pairView = useRef<HTMLDivElement>(null);
  const prompt = useRef<HTMLDivElement>(null);
  const panes = useRef<Record<keyof TaggedDefects, HTMLDivElement | null>>({ a: null, b: null });

  const title = familyTitle(
    families?.find((item) => item.family === family) ?? {
      aspect: null,
      display_name: family ?? '',
    },
  );

  /** Focuses a newly shown pair and scrolls it to the draft's offsets. */
  const placePair = useEffectEvent(() => {
    pairView.current?.focus({ preventScroll: true });
    const offsets = draft?.scroll ?? { window: 0, prompt: 0, a: 0, b: 0 };
    window.scrollTo({ top: offsets.window });
    for (const { key } of SIDES) {
      const pane = panes.current[key];
      if (pane !== null) {
        pane.scrollTop = offsets[key];
      }
    }
  });

  useLayoutEffect(() => {
    placePair();
  }, [pair?.id]);

  const placePrompt = useEffectEvent(() => {
    if (prompt.current !== null) {
      prompt.current.scrollTop = draft?.scroll.prompt ?? 0;
    }
  });

  useLayoutEffect(() => {
    placePrompt();
  }, [pair?.id, draft?.collapsed]);

  const onPageHide = useEffectEvent(() => {
    if (draft !== null) {
      writeDraft({
        ...draft,
        scroll: {
          window: window.scrollY,
          prompt: prompt.current?.scrollTop ?? draft.scroll.prompt,
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
        submit();
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
      <Verification sitekey={sitekey} onReady={verified} />
    ) : voteFailed ? (
      <Button disabled={busy} onClick={submit}>
        Retry vote
      </Button>
    ) : (
      error !== null &&
      family !== undefined && (
        <Button disabled={busy} onClick={reconnect}>
          Retry connection
        </Button>
      )
    );

  if (state === null) {
    if (family !== undefined) {
      return (
        <Screen title="Connecting to voting">
          {sitekey === null && error === null && <p role="status">Checking your browser...</p>}
          <ErrorAlert error={error} />
          <div className="flex flex-col items-start gap-2">{recovery}</div>
        </Screen>
      );
    }

    return <StartScreen families={families} error={error} onStart={start} />;
  }

  if (pair === null || draft === null) {
    return (
      <div
        ref={pairView}
        tabIndex={-1}
        role="group"
        aria-label="Completed family"
        className="outline-none"
      >
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
          scrollRef={prompt}
          collapsed={draft.collapsed}
          onCollapsedChange={(collapsed) => {
            update({
              collapsed,
              scroll: { ...draft.scroll, prompt: prompt.current?.scrollTop ?? draft.scroll.prompt },
            });
          }}
          status={
            <StatusBand
              title={title}
              votes={state.votes}
              remaining={state.remaining}
              saved={saved}
              notice={notice}
            />
          }
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

      {hasText && (
        <div className="flex flex-wrap items-center gap-3">
          <Toggle
            variant="outline"
            pressed={draft.markdown}
            onPressedChange={(markdown) => {
              update({ markdown });
            }}
          >
            Render Markdown
          </Toggle>
        </div>
      )}

      <Ballot
        choice={draft.choice}
        undoable={state.undo}
        locked={locked}
        error={error}
        recovery={sitekey !== null || voteFailed ? recovery : null}
        onChoose={(choice) => {
          update({ choice });
        }}
        onUndo={undo}
        onSkip={skip}
        onSubmit={submit}
      />
    </div>
  );
}
