import { vi } from 'vitest';

/**
 * Uses `width` CSS pixels and seven pixels per label character. Vitest restores
 * `ResizeObserver` and canvas mocks after each test. Fonts and SVG bounds stay stubbed for this file.
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
