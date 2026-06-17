import { ReactNode, useCallback, useMemo, useState } from 'react';
import {
  ResponsiveGridLayout,
  useContainerWidth,
  type Layout,
  type ResponsiveLayouts,
} from 'react-grid-layout';
import { Panel } from 'src/config.context';
import {
  buildResponsiveLayouts,
  RESPONSIVE_BREAKPOINTS,
  RESPONSIVE_COLS,
  type LayoutCoords,
  type ResponsiveBreakpoint,
} from 'src/components/reports/panelLayout';

const ROW_HEIGHT = 48;
const MARGIN: [number, number] = [12, 12];
const CONTAINER_PADDING: [number, number] = [0, 0];

const DRAG_CANCEL_SELECTOR =
  '.MuiIconButton-root, .MuiTextField-root, .MuiButton-root, .MuiTabs-root, .MuiSelect-select, button, input, textarea';

export interface PanelGridRowProps {
  panels: Panel[];
  renderPanel: (panel: Panel, index: number) => ReactNode;
  isEditing?: boolean;
  onLayoutChange?: (
    layoutsByBreakpoint: ResponsiveLayouts<ResponsiveBreakpoint>,
  ) => void;
  className?: string;
}

function panelKey(_panel: Panel, idx: number): string {
  return `${idx}`;
}

/** Convert pixels to grid rows accounting for inter-row margin. */
function pxToRows(px: number): number {
  // rgl cell height = h * rowHeight + (h - 1) * margin_y
  // Solve for h: h = (px + margin) / (rowHeight + margin)
  return Math.max(1, Math.ceil((px + MARGIN[1]) / (ROW_HEIGHT + MARGIN[1])));
}

interface AutoHeightWrapperProps {
  children: ReactNode;
  onHeightChange: (heightPx: number) => void;
}

/**
 * Wraps a panel whose ``auto_height`` is true. Renders the content in a
 * naturally-flowing div (no height clamp) and reports the rendered height
 * via ``ResizeObserver`` so the parent can grow the rgl cell to match.
 */
function AutoHeightWrapper({
  children,
  onHeightChange,
}: AutoHeightWrapperProps) {
  const observe = useCallback(
    (node: HTMLDivElement | null) => {
      if (!node || typeof ResizeObserver === 'undefined') return;
      const observer = new ResizeObserver((entries) => {
        const entry = entries[0];
        if (!entry) return;
        onHeightChange(entry.contentRect.height);
      });
      observer.observe(node);
      // We rely on the wrapper unmounting to clean up; React calls the ref
      // callback with null on unmount, but the observer will already have
      // detached because the node is gone.
    },
    [onHeightChange],
  );

  return (
    <div
      ref={observe}
      style={{ width: '100%', height: 'auto', overflow: 'visible' }}
    >
      {children}
    </div>
  );
}

function PanelGridRow({
  panels,
  renderPanel,
  isEditing = false,
  onLayoutChange,
  className,
}: PanelGridRowProps) {
  const baseLayouts = useMemo(() => buildResponsiveLayouts(panels), [panels]);
  const { width, containerRef, mounted } = useContainerWidth();

  // Pixel heights measured for ``auto_height`` panels, keyed by panel index.
  const [autoHeightsPx, setAutoHeightsPx] = useState<Record<number, number>>(
    {},
  );

  const setAutoHeightForIdx = useCallback((idx: number, heightPx: number) => {
    setAutoHeightsPx((prev) => {
      const rounded = Math.round(heightPx);
      // Avoid update churn from sub-pixel ResizeObserver fluctuations.
      if (prev[idx] === rounded) return prev;
      return { ...prev, [idx]: rounded };
    });
  }, []);

  // Grow the grid cell to fit measured content for ``auto_height`` panels in
  // both view and edit modes — otherwise a content-sized panel is clipped to
  // its (smaller) configured ``h``. Fixed-height panels keep their configured
  // ``h`` and remain user-resizable in edit mode.
  const layouts = useMemo(() => {
    const result = {} as Record<ResponsiveBreakpoint, LayoutCoords[]>;
    const breakpoints: ResponsiveBreakpoint[] = ['lg', 'md', 'sm', 'xs'];
    for (const bp of breakpoints) {
      result[bp] = baseLayouts[bp].map((item, idx) => {
        const panel = panels[idx];
        const px = autoHeightsPx[idx];
        if (panel?.auto_height && px && px > 0) {
          return { ...item, h: pxToRows(px) };
        }
        return item;
      });
    }
    return result;
  }, [baseLayouts, panels, autoHeightsPx]);

  const items = panels.map((panel, idx) => {
    const useAutoHeight = panel.auto_height === true;
    return (
      <div
        key={panelKey(panel, idx)}
        style={{ display: 'flex', flexDirection: 'column', minHeight: 0 }}
      >
        {useAutoHeight ? (
          <AutoHeightWrapper
            onHeightChange={(h) => setAutoHeightForIdx(idx, h)}
          >
            {renderPanel(panel, idx)}
          </AutoHeightWrapper>
        ) : (
          renderPanel(panel, idx)
        )}
      </div>
    );
  });

  return (
    <div
      ref={containerRef}
      className={`report-row${className ? ` ${className}` : ''}`}
    >
      {mounted && (
        <ResponsiveGridLayout<ResponsiveBreakpoint>
          width={width}
          layouts={layouts}
          breakpoints={RESPONSIVE_BREAKPOINTS}
          cols={RESPONSIVE_COLS}
          rowHeight={ROW_HEIGHT}
          margin={MARGIN}
          containerPadding={CONTAINER_PADDING}
          dragConfig={{
            enabled: isEditing,
            cancel: isEditing ? DRAG_CANCEL_SELECTOR : undefined,
          }}
          resizeConfig={{ enabled: isEditing }}
          onLayoutChange={(
            _current: Layout,
            all: ResponsiveLayouts<ResponsiveBreakpoint>,
          ) => onLayoutChange?.(all)}
        >
          {items}
        </ResponsiveGridLayout>
      )}
    </div>
  );
}

export default PanelGridRow;
