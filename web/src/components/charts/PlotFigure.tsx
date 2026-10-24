import * as Plot from '@observablehq/plot';
import { useEffect, useRef, useState } from 'react';

/** Room between a row label and the plot, in CSS pixels. */
export const LABEL_GAP = 12;

export interface PlotFrame {
  /** The container's width, in CSS pixels. */
  width: number;
  /** Returns the width of a label in the chart's font, in CSS pixels. */
  measure: (text: string) => number;
}

interface PlotFigureProps {
  /** Builds the plot for the measured container, which sets its width and height. */
  plot: (frame: PlotFrame) => Plot.PlotOptions;
  /** The chart's height, which the container reserves before the first render, in CSS pixels. */
  height: number;
  /**
   * Names the chart for assistive technology, which reads its values from the table twin. Omit it
   * where an enclosing element already names the chart.
   */
  label?: string;
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

export function PlotFigure({ plot, height, label }: PlotFigureProps) {
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
    // layer, so it beats every layered rule, Tailwind's utilities included.
    const figure = Plot.plot({
      className: 'bench-plot',
      style: '--plot-background: var(--popover)',
      ...options,
      width,
      height,
    });
    figure.setAttribute('aria-hidden', 'true');
    element.replaceChildren(figure);
    return () => {
      figure.remove();
    };
  }, [plot, width, height, fontsReady]);

  // The font utilities sit on the SVG too, since Plot sets its font in presentation attributes.
  return (
    <div
      ref={container}
      role={label === undefined ? undefined : 'img'}
      aria-label={label}
      className="font-sans text-xs text-muted-foreground [&_[aria-label=tip]]:text-popover-foreground [&_svg]:font-sans [&_svg]:text-xs"
      style={{ minHeight: height }}
    />
  );
}
