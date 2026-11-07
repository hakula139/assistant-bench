import { cn } from 'cn';
import {
  ChevronRight,
  Columns2,
  FileDiff,
  FileText,
  FlaskConical,
  ListChecks,
  PenLine,
  Percent,
  Play,
  ShieldCheck,
  Sigma,
  SquareTerminal,
  Vote,
  type LucideIcon,
} from 'lucide-react';

import { Hint } from '@/components/site/Term';
import { aspects, type Aspect } from '@/lib/aspects';
import type { FamilyConfig, Scorer } from '@/lib/family';
import { methodSteps, scorers, type MethodStep } from '@/lib/method';

const stepIcons: Record<MethodStep['step'], LucideIcon> = {
  text: FileText,
  diff: FileDiff,
  trial: Play,
  gates: ShieldCheck,
  pairs: Columns2,
  scores: Sigma,
  tests: ListChecks,
  rate: Percent,
};

const scorerIcons: Record<Scorer, LucideIcon> = {
  votes: Vote,
  tests: FlaskConical,
  checkers: ListChecks,
};

const aspectIcons: Record<Aspect, LucideIcon> = {
  writing: PenLine,
  coding: SquareTerminal,
  instructions: ListChecks,
};

function Glyph({ icon: Icon, className }: { icon: LucideIcon; className?: string }) {
  return <Icon aria-hidden="true" className={cn('size-4 shrink-0', className)} />;
}

export function AspectTitle({ aspect }: { aspect: Aspect }) {
  return (
    <>
      <Glyph icon={aspectIcons[aspect]} className="size-[0.8em]" />
      {aspects[aspect].title}
    </>
  );
}

export function ScorerIcon({ scorer }: { scorer: Scorer }) {
  return <Glyph icon={scorerIcons[scorer]} />;
}

/** `of` gives a family's config, whose steps open hints, or a scorer alone. */
export function MethodStrip({
  of,
  links = {},
}: {
  of: Scorer | FamilyConfig;
  links?: Partial<Record<MethodStep['step'], string>>;
}) {
  const scorer = typeof of === 'string' ? of : of.scorer;

  return (
    <ol
      aria-label={`${scorers[scorer].title} scoring steps`}
      className="flex flex-wrap items-center gap-x-2 gap-y-3 text-sm max-md:flex-col max-md:items-start max-md:gap-y-2"
    >
      {methodSteps(of).map(({ step, title, detail, hint }, i) => {
        const Icon = stepIcons[step];
        const href = links[step];

        return (
          <li key={step} className="flex items-center gap-2 max-md:flex-col max-md:items-start">
            {i > 0 && (
              <ChevronRight
                className="size-4 shrink-0 text-faint max-md:ml-2 max-md:rotate-90"
                aria-hidden="true"
              />
            )}
            <span className="flex items-center gap-2">
              <span className="flex size-8 shrink-0 items-center justify-center rounded-md border text-muted-foreground">
                <Icon className="size-4" aria-hidden="true" />
              </span>
              <span className="leading-tight">
                <span className="block font-medium">
                  {href ? <a href={href}>{title}</a> : title}
                </span>
                <span className="block text-xs text-muted-foreground">
                  {hint ? <Hint term={{ term: title, definition: hint }}>{detail}</Hint> : detail}
                </span>
              </span>
            </span>
          </li>
        );
      })}
    </ol>
  );
}
