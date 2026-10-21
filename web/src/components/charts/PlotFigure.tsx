import * as Plot from '@observablehq/plot';
import { useEffect, useRef, useState } from 'react';

interface PlotFigureProps {
  /** Builds the plot for the measured container width, in CSS pixels. */
  plot: (width: number) => Plot.PlotOptions;
  /** Reserved before the first render so the page does not shift, in CSS pixels. */
  height: number;
  className?: string;
}

/** Renders an Observable Plot chart that fills its container's width. */
export function PlotFigure({ plot, height, className }: PlotFigureProps) {
  const container = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState<number | null>(null);

  useEffect(() => {
    const element = container.current;
    if (!element) {
      return;
    }
    const observer = new ResizeObserver(([entry]) => {
      setWidth(Math.floor(entry.contentRect.width));
    });
    observer.observe(element);
    return () => {
      observer.disconnect();
    };
  }, []);

  useEffect(() => {
    const element = container.current;
    if (!element || width === null) {
      return;
    }
    const figure = Plot.plot({ className: 'bench-plot', ...plot(width), width });
    element.replaceChildren(figure);
    return () => {
      figure.remove();
    };
  }, [plot, width]);

  return <div ref={container} className={className} style={{ minHeight: height }} />;
}
