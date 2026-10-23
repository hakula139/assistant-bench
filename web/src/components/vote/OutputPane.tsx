import { cn } from 'cn';
import { useEffect, useRef, useState } from 'react';

import { Markdown, PlainText } from '@/components/vote/Markdown';
import { band, panel, scroll } from '@/components/vote/panel';
import { Card, CardContent, CardTitle } from '@/components/ui/card';
import type { Pane } from '@/lib/arena';

interface OutputPaneProps {
  side: 'A' | 'B';
  pane: Pane;
  /** Renders text outputs as Markdown, with raw HTML disabled. */
  asMarkdown: boolean;
  /** The width an HTML output lays out at, scaled down to fit the pane, in CSS pixels. */
  viewport: number;
}

function FramedOutput({ url, viewport, side }: { url: string; viewport: number; side: string }) {
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

/** One side of a pair, shown exactly as written unless the Markdown view is on. */
export function OutputPane({ side, pane, asMarkdown, viewport }: OutputPaneProps) {
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
      </div>
      {pane.render === 'html' ? (
        <CardContent className="min-h-0 flex-1 p-0">
          <FramedOutput url={pane.url} viewport={viewport} side={side} />
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
