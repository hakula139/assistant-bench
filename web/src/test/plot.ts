import { vi } from 'vitest';

/**
 * Stubs what `PlotFigure` and Plot's tips read from the browser, so a chart draws once in jsdom at
 * `width` with labels 7 px per character. The Vitest config restores the observer and the canvas
 * after each test, while `document.fonts` and `getBBox` stay stubbed for the rest of the file.
 */
export function stubPlotFrame(width = 600) {
  vi.stubGlobal(
    'ResizeObserver',
    class {
      constructor(private readonly callback: ResizeObserverCallback) {}
      observe() {
        this.callback([{ contentRect: { width } } as ResizeObserverEntry], this as never);
      }
      disconnect() {}
    },
  );
  Object.defineProperty(document, 'fonts', {
    configurable: true,
    value: { ready: Promise.resolve() },
  });
  Object.defineProperty(SVGElement.prototype, 'getBBox', {
    configurable: true,
    value: () => ({ x: 0, y: 0, width: 0, height: 0 }),
  });
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({
    font: '',
    measureText: (text: string) => ({ width: text.length * 7 }),
  } as never);
}
