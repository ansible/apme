import "@testing-library/jest-dom/vitest";
import { vi } from "vitest";

// Pure unit tests may use @vitest-environment node (no DOM).
if (typeof window !== "undefined") {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    configurable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
  });

  /** jsdom does not implement EventSource; stub for SSE hooks in AppShell tests. */
  class MockEventSource {
    static readonly CONNECTING = 0;
    static readonly OPEN = 1;
    static readonly CLOSED = 2;
    readonly url: string;
    readyState = MockEventSource.CONNECTING;
    onmessage: ((ev: MessageEvent) => void) | null = null;
    onerror: ((ev: Event) => void) | null = null;
    onopen: ((ev: Event) => void) | null = null;
    constructor(url: string) {
      this.url = url;
    }
    addEventListener(): void {}
    removeEventListener(): void {}
    close(): void {
      this.readyState = MockEventSource.CLOSED;
    }
    dispatchEvent(): boolean {
      return false;
    }
  }

  vi.stubGlobal("EventSource", MockEventSource);
}
