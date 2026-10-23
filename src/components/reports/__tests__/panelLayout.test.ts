import { Panel } from 'src/config.context';
import {
  buildResponsiveLayouts,
  defaultMinHeight,
  defaultPanelHeight,
  defaultPanelWidth,
  deriveRowLayout,
  mergePanelLayout,
} from '../panelLayout';

describe('defaultPanelHeight', () => {
  it('returns the per-type default for known panel types', () => {
    expect(defaultPanelHeight('count')).toBe(4);
    expect(defaultPanelHeight('progress')).toBe(4);
    expect(defaultPanelHeight('markdown')).toBe(6);
    expect(defaultPanelHeight('bar')).toBe(8);
    expect(defaultPanelHeight('pie')).toBe(8);
    expect(defaultPanelHeight('table')).toBe(10);
    expect(defaultPanelHeight('vertical-table')).toBe(10);
    expect(defaultPanelHeight('graph')).toBe(12);
  });

  it('falls back for unknown types', () => {
    expect(defaultPanelHeight('unknown')).toBe(8);
  });
});

describe('defaultMinHeight', () => {
  it('returns the per-type minimum height', () => {
    expect(defaultMinHeight('count')).toBe(3);
    expect(defaultMinHeight('table')).toBe(6);
    expect(defaultMinHeight('graph')).toBe(6);
    expect(defaultMinHeight('bar')).toBe(5);
  });

  it('falls back for unknown types', () => {
    expect(defaultMinHeight('unknown')).toBe(3);
  });
});

describe('defaultPanelWidth', () => {
  it('uses 3 when w is unset', () => {
    expect(defaultPanelWidth({ type: 'count' })).toBe(3);
  });

  it('clamps to 1..cols', () => {
    expect(defaultPanelWidth({ type: 'count', w: 99 })).toBe(12);
    expect(defaultPanelWidth({ type: 'count', w: 0 })).toBe(1);
    expect(defaultPanelWidth({ type: 'count', w: -3 })).toBe(1);
  });
});

describe('deriveRowLayout', () => {
  it('packs panels left-to-right within a 12-col grid', () => {
    const panels: Panel[] = [
      { type: 'count', w: 3 },
      { type: 'count', w: 4 },
      { type: 'count', w: 5 },
    ];
    const layout = deriveRowLayout(panels);

    expect(layout).toHaveLength(3);
    expect(layout[0]).toMatchObject({ x: 0, y: 0, w: 3 });
    expect(layout[1]).toMatchObject({ x: 3, y: 0, w: 4 });
    expect(layout[2]).toMatchObject({ x: 7, y: 0, w: 5 });
  });

  it('wraps to a new row when cumulative width exceeds cols', () => {
    const panels: Panel[] = [
      { type: 'count', w: 8 },
      { type: 'count', w: 5 },
    ];
    const layout = deriveRowLayout(panels);

    expect(layout[0]).toMatchObject({ x: 0, y: 0 });
    expect(layout[1]).toMatchObject({ x: 0, y: 1 });
  });

  it('honors explicit x and y coordinates', () => {
    const panels: Panel[] = [
      { type: 'count', w: 3, x: 6, y: 0 },
      { type: 'count', w: 3, x: 9, y: 0 },
    ];
    const layout = deriveRowLayout(panels);

    expect(layout[0]).toMatchObject({ x: 6, y: 0, w: 3 });
    expect(layout[1]).toMatchObject({ x: 9, y: 0, w: 3 });
  });

  it('applies per-type default heights when h is unset', () => {
    const panels: Panel[] = [
      { type: 'count' },
      { type: 'table' },
      { type: 'graph' },
    ];
    const layout = deriveRowLayout(panels);

    expect(layout[0].h).toBe(4);
    expect(layout[1].h).toBe(10);
    expect(layout[2].h).toBe(12);
  });

  it('honors explicit h when set', () => {
    const panels: Panel[] = [{ type: 'count', h: 9 }];
    const layout = deriveRowLayout(panels);

    expect(layout[0].h).toBe(9);
  });

  it('honors explicit min_h when set', () => {
    const panels: Panel[] = [{ type: 'count', min_h: 7 }];
    const layout = deriveRowLayout(panels);

    expect(layout[0].minH).toBe(7);
  });
});

describe('buildResponsiveLayouts', () => {
  it('returns layouts for lg, md, sm, xs', () => {
    const panels: Panel[] = [{ type: 'count', w: 3 }];
    const layouts = buildResponsiveLayouts(panels);

    expect(Object.keys(layouts).sort()).toEqual(['lg', 'md', 'sm', 'xs']);
  });

  it('gives charts, tables and graphs the full width below md', () => {
    const panels: Panel[] = [
      { type: 'graph', w: 7 },
      { type: 'table', w: 5 },
      { type: 'pie', w: 3 },
    ];
    const layouts = buildResponsiveLayouts(panels);

    expect(layouts.lg.map((l) => l.w)).toEqual([7, 5, 3]);
    for (const bp of ['sm', 'xs'] as const) {
      expect(layouts[bp].map((l) => l.w)).toEqual([12, 12, 12]);
      expect(layouts[bp].map((l) => l.y)).toEqual([0, 1, 2]);
    }
  });

  it('pairs stat panels on a phone and sets three across on a tablet', () => {
    const panels: Panel[] = [
      { type: 'count', w: 3 },
      { type: 'count', w: 3 },
      { type: 'progress', w: 3 },
      { type: 'count', w: 3 },
    ];
    const layouts = buildResponsiveLayouts(panels);

    expect(layouts.xs.map((l) => [l.x, l.y, l.w])).toEqual([
      [0, 0, 6],
      [6, 0, 6],
      [0, 1, 6],
      [6, 1, 6],
    ]);
    expect(layouts.sm.map((l) => [l.x, l.y, l.w])).toEqual([
      [0, 0, 4],
      [4, 0, 4],
      [8, 0, 4],
      // Alone on its row, so it takes the row.
      [0, 1, 12],
    ]);
  });

  it('spreads a short last row of stat panels across the width', () => {
    const panels: Panel[] = [
      { type: 'count', w: 3 },
      { type: 'count', w: 3 },
      { type: 'count', w: 3 },
      { type: 'table', w: 12 },
      { type: 'count', w: 3 },
      { type: 'count', w: 3 },
    ];
    const layouts = buildResponsiveLayouts(panels);

    expect(layouts.xs.map((l) => l.w)).toEqual([6, 6, 12, 12, 6, 6]);
    expect(layouts.sm.map((l) => l.w)).toEqual([4, 4, 4, 12, 6, 6]);
  });

  it('stacks panels in the order they read on a wide screen', () => {
    // Authored out of order: the panel listed first sits below the others.
    const panels: Panel[] = [
      { type: 'table', x: 0, y: 4, w: 12 },
      { type: 'markdown', x: 0, y: 0, w: 6 },
      { type: 'bar', x: 6, y: 0, w: 6 },
    ];
    const layouts = buildResponsiveLayouts(panels);

    // Keyed by panel, positioned by reading order.
    expect(layouts.xs.map((l) => l.i)).toEqual(['0', '1', '2']);
    expect(layouts.xs.map((l) => l.y)).toEqual([2, 0, 1]);
  });
});

describe('mergePanelLayout', () => {
  const POS = { x: 1, y: 2, w: 4, h: 6 };

  it('returns null when nothing changed', () => {
    const panel: Panel = { type: 'count', x: 1, y: 2, w: 4, h: 6 };
    expect(mergePanelLayout(panel, POS)).toBeNull();
  });

  it('persists x/y/w/h for a fixed-height panel that moved or resized', () => {
    const panel: Panel = { type: 'count', x: 0, y: 0, w: 3, h: 4 };
    expect(mergePanelLayout(panel, POS)).toEqual({
      type: 'count',
      x: 1,
      y: 2,
      w: 4,
      h: 6,
    });
  });

  it('treats a fixed-height-only change as a change', () => {
    const panel: Panel = { type: 'count', x: 1, y: 2, w: 4, h: 4 };
    expect(mergePanelLayout(panel, POS)?.h).toBe(6);
  });

  it('preserves the configured height for an auto-height panel and ignores h changes', () => {
    const panel: Panel = {
      type: 'markdown',
      auto_height: true,
      x: 1,
      y: 2,
      w: 4,
      h: 5,
    };
    // Only h differs (the grid grew the cell to fit content) → no real change.
    expect(mergePanelLayout(panel, POS)).toBeNull();
  });

  it('persists x/y/w but keeps the configured h when an auto-height panel moves', () => {
    const panel: Panel = {
      type: 'markdown',
      auto_height: true,
      x: 0,
      y: 0,
      w: 3,
      h: 5,
    };
    expect(mergePanelLayout(panel, POS)).toEqual({
      type: 'markdown',
      auto_height: true,
      x: 1,
      y: 2,
      w: 4,
      h: 5,
    });
  });

  it('preserves unrelated fields and identity keys (e.g. _id)', () => {
    const panel = {
      _id: 'p1',
      type: 'table',
      cypher: 'q',
      x: 0,
      y: 0,
      w: 3,
      h: 4,
    };
    expect(mergePanelLayout(panel, POS)).toEqual({
      _id: 'p1',
      type: 'table',
      cypher: 'q',
      x: 1,
      y: 2,
      w: 4,
      h: 6,
    });
  });
});
