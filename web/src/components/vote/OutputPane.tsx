import { cn } from 'cn';
import { useEffect, useRef, useState, type ReactNode } from 'react';

import { Card, CardContent, CardTitle } from '@/components/ui/card';
import { Markdown, PlainText } from '@/components/vote/Markdown';
import { band, panel, scroll } from '@/components/vote/panel';
import type { Pane } from '@/lib/arena';

interface OutputPaneProps {
  side: 'A' | 'B';
  pane: Pane;
  /** Renders text outputs as Markdown, with raw HTML disabled. */
  asMarkdown: boolean;
  /** The width an HTML output lays out at, scaled down to fit the pane, in CSS pixels. */
  viewport: number;
  /** Controls shown at the end of the title band, such as the output's Issues button. */
  controls: ReactNode;
}

function FramedOutput({
  url,
  viewport,
  side,
}: {
  url: string;
  viewport: number;
  side: OutputPaneProps['side'];
}) {
  const container = useRef<HTMLDivElement>(null);
  const [scale, setScale] = useState(1);

  useEffect(() => {
    const element = container.current;
    if (!element) {
      return;
    }
    const observer = new ResizeObserver(([entry]) => {
      setScale(Math.min(1, entry.contentRect.width / viewport));
    });
    observer.observe(element);
    return () => {
      observer.disconnect();
    };
  }, [viewport]);

  return (
    <div ref={container} className="h-full overflow-hidden">
      <iframe
        src={url}
        title={`Output ${side}`}
        sandbox="allow-scripts"
        referrerPolicy="no-referrer"
        className="block origin-top-left border-0 bg-white"
        style={{
          width: viewport,
          marginInline: scale === 1 ? 'auto' : 0,
          height: `${100 / scale}%`,
          transform: `scale(${scale})`,
        }}
      />
    </div>
  );
}

type DiffLine = 'file' | 'hunk' | 'added' | 'removed' | 'context';

const diffLineClasses: Record<DiffLine, string> = {
  file: 'bg-muted font-semibold data-start:not-first:mt-4',
  hunk: 'bg-muted text-muted-foreground',
  added: 'bg-emerald-500/15',
  removed: 'bg-red-500/15',
  context: '',
};

/**
 * Classifies each line of a unified diff. A file's header runs from its `diff --git` line to its
 * first hunk, so a removed line that reads `--- ` stays a removal.
 */
function classifyDiff(text: string): { line: string; kind: DiffLine; start: boolean }[] {
  let inHunk = false;
  return text
    .replace(/\n$/, '')
    .split('\n')
    .map((line) => {
      const start = line.startsWith('diff --git ');
      if (start) {
        inHunk = false;
      } else if (line.startsWith('@@')) {
        inHunk = true;
        return { line, kind: 'hunk', start };
      }
      if (!inHunk) {
        return { line, kind: 'file', start };
      }
      const kind = line.startsWith('+') ? 'added' : line.startsWith('-') ? 'removed' : 'context';
      return { line, kind, start };
    });
}

/**
 * A unified diff in monospace, each line styled as a file header, hunk header, or change. Wrapped
 * lines hang past the change marker, and ligatures stay off so `==` and `->` read as typed.
 */
function Diff({ text }: { text: string }) {
  return (
    <div className="font-mono text-sm leading-relaxed wrap-anywhere whitespace-pre-wrap [font-variant-ligatures:none]">
      {classifyDiff(text).map(({ line, kind, start }, index) => (
        <div
          key={index}
          data-diff={kind}
          data-start={start || undefined}
          className={cn(
            'min-h-lh ps-[calc(var(--card-spacing)+1ch)] pe-(--card-spacing) -indent-[1ch]',
            diffLineClasses[kind],
          )}
        >
          {line}
        </div>
      ))}
    </div>
  );
}

/** One side of a pair, shown exactly as written unless the Markdown view is on. */
export function OutputPane({ side, pane, asMarkdown, viewport, controls }: OutputPaneProps) {
  return (
    <Card
      size="sm"
      role="region"
      aria-label={`Output ${side}`}
      className={cn(panel, 'text-base md:min-h-0', pane.render === 'html' && 'max-md:h-[60dvh]')}
    >
      <div className={cn(band, 'border-b')}>
        <CardTitle role="heading" aria-level={2}>
          Output {side}
        </CardTitle>
        <div className="ml-auto">{controls}</div>
      </div>
      {pane.render === 'html' ? (
        <CardContent className="min-h-0 flex-1 p-0">
          <FramedOutput url={pane.url} viewport={viewport} side={side} />
        </CardContent>
      ) : pane.render === 'diff' ? (
        <CardContent className={cn(scroll, 'px-0')}>
          <Diff text={pane.text} />
        </CardContent>
      ) : asMarkdown ? (
        <CardContent className={scroll}>
          <Markdown text={pane.text} />
        </CardContent>
      ) : (
        <CardContent className={cn(scroll, 'leading-relaxed whitespace-pre-wrap')}>
          <PlainText text={pane.text} />
        </CardContent>
      )}
    </Card>
  );
}
