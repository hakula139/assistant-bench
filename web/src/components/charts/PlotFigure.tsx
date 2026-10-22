import * as Plot from '@observablehq/plot';
import { cn } from 'cn';
import { useEffect, useRef, useState } from 'react';

export interface PlotFrame {
  /** The container's width, in CSS pixels. */
  width: number;
  /** Returns the width of a label in the chart's font, in CSS pixels. */
  measure: (text: string) => number;
}

interface PlotFigureProps {
  /** Builds the plot for the measured container. */
  plot: (frame: PlotFrame) => Plot.PlotOptions;
  /** Reserved before the first render so the page does not shift, in CSS pixels. */
  height: number;
  className?: string;
}

function labelMeasure(element: HTMLElement): (text: string) => number {
  const context = document.createElement('canvas').getContext('2d');
  if (context === null) {
    throw new Error('the browser offers no 2D canvas to measure labels with');
  }
  const style = getComputedStyle(element);
  context.font = `${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
  return (text) => Math.ceil(context.measureText(text).width);
}

/** Renders an Observable Plot chart that fills its container's width. */
export function PlotFigure({ plot, height, className }: PlotFigureProps) {
  const container = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState<number | null>(null);
  const [fontsReady, setFontsReady] = useState(false);

  useEffect(() => {
    const element = container.current;
    if (!element) {
      return;
    }
    const observer = new ResizeObserver(([entry]) => {
      setWidth(Math.floor(entry.contentRect.width));
    });
    observer.observe(element);
    void document.fonts.ready.then(() => {
      setFontsReady(true);
    });
    return () => {
      observer.disconnect();
    };
  }, []);

  useEffect(() => {
    const element = container.current;
    if (!element || width === null || !fontsReady) {
      return;
    }
    const options = plot({ width, measure: labelMeasure(element) });
    // Plot's injected :where(.bench-plot) { --plot-background: white } sits outside any cascade
    // layer, so it beats every layered rule in global.css.
    const figure = Plot.plot({
      className: 'bench-plot',
      style: '--plot-background: var(--popover)',
      ...options,
      width,
    });
    element.replaceChildren(figure);
    return () => {
      figure.remove();
    };
  }, [plot, width, fontsReady]);

  return (
    <div ref={container} className={cn('bench-plot', className)} style={{ minHeight: height }} />
  );
}
