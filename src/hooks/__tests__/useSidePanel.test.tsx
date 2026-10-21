import { useState } from 'react';
import { act, renderHook } from '@testing-library/react';
import { useSidePanel } from 'src/hooks/useSidePanel';

function useStoredPanel() {
  const [stored, setStored] = useState(true);
  return { stored, panel: useSidePanel(stored, setStored) };
}

function stubViewport(narrow: boolean) {
  window.matchMedia = ((query: string) => ({
    matches: narrow,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  })) as typeof window.matchMedia;
}

describe('useSidePanel', () => {
  const original = window.matchMedia;
  afterEach(() => {
    window.matchMedia = original;
  });

  it('follows the stored state beside the page', () => {
    stubViewport(false);
    const { result } = renderHook(useStoredPanel);
    expect(result.current.panel.overlay).toBe(false);
    expect(result.current.panel.open).toBe(true);

    act(() => result.current.panel.toggle());
    expect(result.current.panel.open).toBe(false);
    expect(result.current.stored).toBe(false);
  });

  it('starts closed as an overlay and closes on dismiss without touching the stored state', () => {
    stubViewport(true);
    const { result } = renderHook(useStoredPanel);
    expect(result.current.panel.overlay).toBe(true);
    expect(result.current.panel.open).toBe(false);

    act(() => result.current.panel.toggle());
    expect(result.current.panel.open).toBe(true);

    act(() => result.current.panel.dismiss());
    expect(result.current.panel.open).toBe(false);
    expect(result.current.stored).toBe(true);
  });
});
