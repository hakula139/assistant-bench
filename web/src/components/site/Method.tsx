import { cn } from 'cn';
import {
  AppWindow,
  ChevronRight,
  Columns2,
  FileText,
  FlaskConical,
  ListChecks,
  PenLine,
  Percent,
  Play,
  ShieldCheck,
  Sigma,
  Vote,
  type LucideIcon,
} from 'lucide-react';

import { Hint } from '@/components/site/Term';
import { aspects, type Aspect } from '@/lib/aspects';
import type { Scorer, VotesConfig } from '@/lib/family';
import { methodSteps, scorers, type MethodStep } from '@/lib/method';

const stepIcons: Record<MethodStep['step'], LucideIcon> = {
  text: FileText,
  html: AppWindow,
  trial: Play,
  gates: ShieldCheck,
  pairs: Columns2,
  scores: Sigma,
  tests: ListChecks,
  rate: Percent,
};

const scorerIcons: Record<Scorer, LucideIcon> = { votes: Vote, tests: FlaskConical };

const aspectIcons: Record<Aspect, LucideIcon> = { writing: PenLine };

/** An aspect's icon, which decorates the aspect's title beside it. */
export function AspectIcon({ aspect, className }: { aspect: Aspect; className?: string }) {
  const Icon = aspectIcons[aspect];
  return <Icon aria-hidden="true" className={cn('size-4 shrink-0', className)} />;
}

/** An aspect's icon and title, the content of a heading that names the aspect. */
export function AspectTitle({ aspect }: { aspect: Aspect }) {
  return (
    <>
      <AspectIcon aspect={aspect} className="size-[0.8em]" />
      {aspects[aspect].title}
    </>
  );
}

/** A scorer's icon, which decorates the scorer's title beside it. */
export function ScorerIcon({ scorer, className }: { scorer: Scorer; className?: string }) {
  const Icon = scorerIcons[scorer];
  return <Icon aria-hidden="true" className={cn('size-4 shrink-0', className)} />;
}

/**
 * A scorer's stages from output to board, one family's when `config` is given and the scorer's in
 * general otherwise, where `links` points a step at the part that explains it.
 */
export function MethodStrip({
  scorer,
  config,
  links = {},
}: {
  scorer: Scorer;
  config?: VotesConfig;
  links?: Partial<Record<MethodStep['step'], string>>;
}) {
  return (
    <ol
      aria-label={`${scorers[scorer].title} scoring steps`}
      className="flex flex-wrap items-center gap-x-2 gap-y-3 text-sm max-sm:flex-col max-sm:items-start"
    >
      {methodSteps(scorer, config).map(({ step, title, detail, hint }, i) => {
        const Icon = stepIcons[step];
        const href = links[step];
        return (
          <li key={step} className="flex items-center gap-2 max-sm:flex-col max-sm:items-start">
            {i > 0 && (
              <ChevronRight
                className="size-4 shrink-0 text-faint max-sm:ml-2 max-sm:rotate-90"
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
                  {hint ? <Hint term={hint}>{detail}</Hint> : detail}
                </span>
              </span>
            </span>
          </li>
        );
      })}
    </ol>
  );
}
