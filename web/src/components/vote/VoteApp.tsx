import { cn } from 'cn';
import { Check, ChevronDown, CircleCheck } from 'lucide-react';
import { useEffect, useEffectEvent, useId, useRef, useState, type ReactNode } from 'react';

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
import {
  ApiError,
  getStatus,
  listFamilies,
  NetworkError,
  type Choice,
  type FamilyView,
  type PairView,
  type SessionView,
  type TaggedDefects,
  type VoteAcknowledgment,
} from '@/lib/arena';
import { familyTitle, groupByAspect, isAspect } from '@/lib/aspects';
import { formatCountOf } from '@/lib/format';
import type { Defect } from '@/lib/report';
import { VoteFlow } from '@/lib/vote-flow';

const FAMILY_PARAM = 'family';
const SAVED_MS = 1500;
const STALE_PAIR = 'This pair changed in another tab.';
const GONE_FAMILY = 'This family has no outputs available to vote on.';
const VIEWPORTS = [375, 768, 1280] as const;

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

const UNTAGGED: TaggedDefects = { a: [], b: [] };

/** Five shortcut keys per pane match the family's maximum defect count. */
const DEFECT_KEYS: Record<keyof TaggedDefects, readonly string[]> = {
  a: ['1', '2', '3', '4', '5'],
  b: ['6', '7', '8', '9', '0'],
};

function keyedDefects(defects: readonly Defect[]) {
  return new Map(
    SIDES.flatMap(({ key }) =>
      defects.map((defect, index) => [DEFECT_KEYS[key][index], { key, defect }] as const),
    ),
  );
}

const notFound = (reason: unknown) => reason instanceof ApiError && reason.status === 404;

function message(error: unknown): ReactNode {
  return error instanceof ApiError || error instanceof NetworkError
    ? error.message
    : 'The voting service is unavailable. Retry to check the same request.';
}

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
    <div className="mx-auto flex w-full max-w-xl flex-col gap-4 px-4 py-16">
      <h1 className={pageTitle}>{title}</h1>
      {children}
    </div>
  );
}

interface StartScreenProps {
  busy: boolean;
  error: ReactNode;
  onStart: (family: string) => void;
}

function StartScreen({ busy, error: startError, onStart }: StartScreenProps) {
  const groupsId = useId();
  const [families, setFamilies] = useState<FamilyView[] | null>(null);
  const [error, setError] = useState<ReactNode>(null);

  useEffect(() => {
    listFamilies().then(setFamilies, (reason: unknown) => {
      setError(message(reason));
    });
  }, []);

  return (
    <Screen title="Start a voting session">
      <ErrorAlert error={error === null ? startError : <>Could not list families: {error}</>} />
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
                  <li key={family.name}>
                    <Button
                      variant="outline"
                      disabled={busy}
                      onClick={() => {
                        onStart(family.name);
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

function Brief({ pair, status, stacked }: { pair: PairView; status: ReactNode; stacked: boolean }) {
  const checklistTitle = useId();
  // Keep this component mounted across pairs so the live region announces changes.
  const [collapsed, setCollapsed] = useState<string | null>(null);

  return (
    <Collapsible
      open={collapsed !== pair.id}
      onOpenChange={(next) => {
        setCollapsed(next ? null : pair.id);
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
              stacked ? 'max-h-[25dvh]' : 'max-xl:max-h-[25dvh]',
            )}
          >
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

export function VoteApp() {
  const [flow] = useState(() => new VoteFlow());
  const [session, setSession] = useState<SessionView | null>(null);
  const [admission, setAdmission] = useState(false);
  const [acceptedReceipt, setAcceptedReceipt] = useState<string | null>(null);

  // The page renders only in the browser, so its URL is readable on the first render.
  const [resumingFamily, setResumingFamily] = useState(
    () => new URLSearchParams(window.location.search).get(FAMILY_PARAM) ?? flow.family,
  );

  const [error, setError] = useState<ReactNode>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [savedVote, setSavedVote] = useState<{ sessionId: string; at: number } | null>(null);
  const [choice, setChoice] = useState<Choice | null>(null);
  const [tagged, setTagged] = useState<TaggedDefects>(UNTAGGED);
  const [asMarkdown, setAsMarkdown] = useState(false);
  const [viewport, setViewport] = useState<number>(VIEWPORTS[2]);
  const [frameFocused, setFrameFocused] = useState(false);
  const [busy, setBusy] = useState(false);
  const [checking, setChecking] = useState(true);
  const pairView = useRef<HTMLDivElement>(null);

  const pair = session?.pair ?? null;
  const familyDefects = session?.family.defects ?? [];
  const hasFrames = pair !== null && (pair.a.render === 'html' || pair.b.render === 'html');
  const hasText = pair !== null && (pair.a.render === 'text' || pair.b.render === 'text');
  const votingLocked = busy || admission || acceptedReceipt !== null || flow.withdrawing;

  useEffect(() => {
    pairView.current?.focus({ preventScroll: true });
    window.scrollTo({ top: 0 });
  }, [pair?.id]);

  const show = (next: SessionView) => {
    const { changed } = flow.show(next);
    history.replaceState(null, '', `?${new URLSearchParams({ [FAMILY_PARAM]: next.family.name })}`);

    setSession(next);
    setAcceptedReceipt(null);
    setSavedVote((current) => (current?.sessionId === next.id ? current : null));
    setChoice(flow.draft?.choice ?? null);
    setTagged(flow.draft?.defects ?? UNTAGGED);
    setError(null);
    setNotice(changed ? STALE_PAIR : null);
  };

  const failed = (reason: unknown) => {
    if (
      reason instanceof ApiError &&
      (reason.code === 'admission_required' || reason.code === 'renewal_required')
    ) {
      setAdmission(true);
    }
    setError(message(reason));
  };

  const accepted = (receipt: VoteAcknowledgment) => {
    flow.accepted();
    setAcceptedReceipt(receipt.acknowledgment);
    setSavedVote({ sessionId: receipt.session_id, at: Date.now() });
    setNotice('Vote accepted.');
    setChoice(null);
    setTagged(UNTAGGED);
  };

  const follow = async (family: string) => {
    try {
      show(await flow.enter(family, accepted));
    } catch (reason) {
      failed(reason);
    }
  };

  const run = async (action: () => Promise<SessionView>) => {
    setBusy(true);
    try {
      show(await action());
    } catch (reason) {
      if (session !== null && notFound(reason)) {
        await follow(session.family.name);
      } else if (notFound(reason)) {
        setError(GONE_FAMILY);
      } else {
        failed(reason);
      }
    } finally {
      setBusy(false);
    }
  };

  const initialize = useEffectEvent(() => {
    getStatus()
      .then(async (status) => {
        if (status.status !== 'ready') {
          setAdmission(true);
        }
        // Receipt resolution remains available while a challenge grant is stale.
        if (resumingFamily !== null) {
          show(await flow.enter(resumingFamily, accepted));
        }
      }, failed)
      .catch((reason: unknown) => {
        failed(reason);
      })
      .finally(() => {
        setResumingFamily(null);
        setChecking(false);
      });
  });

  useEffect(() => {
    initialize();
  }, []);

  useEffect(() => {
    if (session !== null && acceptedReceipt === null) {
      flow.choose(session, choice, tagged);
    }
  }, [session, choice, tagged, flow, acceptedReceipt]);

  useEffect(() => {
    if (savedVote === null) {
      return;
    }

    const timer = setTimeout(() => {
      setSavedVote(null);
    }, SAVED_MS);

    return () => {
      clearTimeout(timer);
    };
  }, [savedVote]);

  useEffect(() => {
    const onBlur = () => {
      setFrameFocused(document.activeElement instanceof HTMLIFrameElement);
    };
    const onFocus = () => {
      setFrameFocused(false);
    };

    window.addEventListener('blur', onBlur);
    window.addEventListener('focus', onFocus);

    return () => {
      window.removeEventListener('blur', onBlur);
      window.removeEventListener('focus', onFocus);
    };
  }, []);

  const submit = () => {
    if (pair === null || choice === null || votingLocked) {
      return;
    }

    if (session === null) {
      return;
    }
    setBusy(true);
    void flow
      .submit(session, choice, tagged)
      .then(
        async (receipt) => {
          accepted(receipt);
          try {
            show(await flow.resume(receipt.session_id));
            setAcceptedReceipt(null);
          } catch (reason) {
            failed(reason);
          }
        },
        async (reason: unknown) => {
          if (
            reason instanceof ApiError &&
            (reason.code === 'conflict' || reason.code === 'not_found')
          ) {
            await follow(session.family.name);
          } else {
            failed(reason);
          }
        },
      )
      .finally(() => {
        setBusy(false);
      });
  };

  const undo = () => {
    if (
      session === null ||
      session.undo_acknowledgment === null ||
      votingLocked ||
      flow.uncertain
    ) {
      return;
    }

    void run(async () => {
      await flow.withdraw(session);
      setSavedVote(null);
      setAcceptedReceipt(null);
      setNotice('Vote withdrawn.');
      return flow.resume(session.id);
    });
  };

  const toggleDefect = (side: keyof TaggedDefects, defect: Defect) => {
    if (votingLocked || flow.uncertain) {
      return;
    }
    setTagged((current) => ({
      ...current,
      [side]: familyDefects.filter((item) => (item === defect) !== current[side].includes(item)),
    }));
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
      if (target === pairView.current || target === document.body) {
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
    if (flow.uncertain || votingLocked) {
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

    const picked = CHOICES.find((item) => item.key === key);
    const keyed = keyedDefects(familyDefects).get(event.key);
    if (picked) {
      setChoice(picked.value);
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

  const currentFamily =
    session?.family.name ??
    resumingFamily ??
    flow.family ??
    new URLSearchParams(window.location.search).get(FAMILY_PARAM);
  const recovery = (
    <div className="flex flex-wrap items-center gap-2">
      {acceptedReceipt !== null && <p role="status">Vote accepted.</p>}
      {admission ? (
        <Button asChild>
          <a
            href={`/admit${currentFamily === null ? '' : `?${new URLSearchParams({ family: currentFamily })}`}`}
          >
            Continue through admission
          </a>
        </Button>
      ) : (
        error !== null && (
          <Button
            disabled={busy}
            onClick={() => {
              if (currentFamily !== null) {
                void run(() => flow.enter(currentFamily, accepted));
              } else {
                window.location.reload();
              }
            }}
          >
            Retry connection
          </Button>
        )
      )}
    </div>
  );

  if (checking) {
    return (
      <Screen title="Connecting to voting">
        <p role="status">Checking admission...</p>
      </Screen>
    );
  }

  if (session === null) {
    return (
      <>
        <div className="mx-auto pt-4">{recovery}</div>
        <StartScreen
          busy={busy || admission}
          error={error}
          onStart={(family) => {
            history.replaceState(null, '', `?${new URLSearchParams({ family })}`);
            void run(() => flow.enter(family, accepted));
          }}
        />
      </>
    );
  }

  if (pair === null) {
    return (
      <div ref={pairView} tabIndex={-1} role="group" aria-label="Completed session">
        <Screen title="Session complete">
          <p className="text-muted-foreground">
            {formatCountOf(session.votes, 'vote')} recorded on {familyTitle(session.family)}.
          </p>
          <Notice notice={notice} />
          <ErrorAlert error={error} />
          {recovery}
          <div className="flex flex-wrap gap-2">
            <Button
              disabled={votingLocked}
              onClick={() => {
                void run(() => flow.start(session.family.name));
              }}
            >
              Start another session
            </Button>
            <Button variant="outline" className="text-foreground" asChild>
              <a href={window.location.pathname}>Choose another family</a>
            </Button>
            <Button
              variant="outline"
              onClick={undo}
              disabled={session.undo_acknowledgment === null || votingLocked}
            >
              Undo last vote
            </Button>
          </div>
        </Screen>
      </div>
    );
  }

  const status = (
    <div className="ml-auto flex flex-wrap items-center gap-x-4 gap-y-2 text-sm text-muted-foreground">
      <span role="status" className="flex items-center gap-1 empty:sr-only">
        {savedVote?.sessionId === session.id ? (
          <>
            <Check className="size-4" aria-hidden="true" />
            Saved
          </>
        ) : (
          notice
        )}
      </span>
      <span className="whitespace-nowrap tabular-nums">
        {familyTitle(session.family)},{' '}
        <Hint
          term={{
            term: 'This session',
            definition: `${formatCountOf(session.remaining, 'pair')} left to vote on.`,
          }}
        >
          {formatCountOf(session.votes, 'vote')}
        </Hint>
      </span>

      {hasFrames && (
        <ToggleGroup
          type="single"
          size="sm"
          variant="outline"
          spacing={0}
          value={String(viewport)}
          onValueChange={(value) => {
            if (value !== '') {
              setViewport(Number(value));
            }
          }}
          aria-label="Viewport width"
        >
          {VIEWPORTS.map((width) => (
            <ToggleGroupItem key={width} value={String(width)}>
              {width} px
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
      )}
    </div>
  );

  return (
    <div
      ref={pairView}
      tabIndex={-1}
      role="group"
      aria-label={`Pair ${session.votes + 1}`}
      className={cn(wideContainer, 'flex flex-col gap-3 py-3 outline-none md:min-h-0 md:flex-1')}
    >
      <div
        className={cn(
          'grid gap-3 md:min-h-0 md:flex-1 md:grid-cols-2 md:grid-rows-[auto_minmax(0,1fr)]',
          hasText &&
            'xl:grid-cols-[minmax(0,24rem)_minmax(0,1fr)_minmax(0,1fr)] xl:gap-y-0 xl:*:row-span-2 xl:*:grid xl:*:grid-rows-subgrid',
        )}
      >
        <Brief pair={pair} status={status} stacked={!hasText} />
        {SIDES.map(({ side, key }) => (
          <OutputPane
            key={`${key}-${pair.id}`}
            side={side}
            pane={pair[key]}
            asMarkdown={asMarkdown}
            viewport={viewport}
            controls={
              familyDefects.length > 0 && (
                <DefectPicker
                  side={side}
                  defects={familyDefects}
                  tagged={tagged[key]}
                  disabled={votingLocked || flow.uncertain}
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
        {frameFocused && (
          <p role="status" className="text-sm text-muted-foreground">
            Keys go to the output frame while it has focus. Click outside the frame to vote by
            keyboard.
          </p>
        )}
        <ErrorAlert error={error} />
        {recovery}

        <div className="flex flex-wrap items-center gap-3">
          {hasText && (
            <Toggle variant="outline" pressed={asMarkdown} onPressedChange={setAsMarkdown}>
              Markdown
            </Toggle>
          )}
        </div>
      </div>

      <div
        className={cn(
          bleed,
          'grid gap-2 max-md:sticky max-md:bottom-0 max-md:border-t max-md:bg-background max-md:py-3 sm:grid-cols-[1fr_auto]',
        )}
      >
        <ToggleGroup
          type="single"
          variant="choice"
          size="lg"
          spacing={2}
          value={choice ?? ''}
          disabled={votingLocked || flow.uncertain}
          onValueChange={(value) => {
            setChoice(CHOICES.find((item) => item.value === value)?.value ?? null);
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
            disabled={session.undo_acknowledgment === null || votingLocked || flow.uncertain}
            aria-keyshortcuts="U"
          >
            Undo
            <Kbd aria-hidden="true">U</Kbd>
          </Button>
          <Button
            size="lg"
            onClick={submit}
            disabled={choice === null || votingLocked}
            aria-keyshortcuts="Enter"
            className="max-sm:flex-1"
          >
            {flow.uncertain ? 'Retry vote' : 'Submit vote'}
            <Kbd aria-hidden="true" className="bg-primary-foreground text-primary">
              ↵
            </Kbd>
          </Button>
        </div>
      </div>
    </div>
  );
}
