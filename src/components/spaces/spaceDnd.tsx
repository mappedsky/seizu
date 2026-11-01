import { useState, type ReactNode } from 'react';
import {
  DndContext,
  DragOverlay,
  KeyboardSensor,
  MeasuringStrategy,
  PointerSensor,
  pointerWithin,
  rectIntersection,
  useDndContext,
  useDraggable,
  useDroppable,
  useSensor,
  useSensors,
  type Active,
  type CollisionDetection,
  type DragEndEvent,
  type DragStartEvent,
  type KeyboardCoordinateGetter,
  type Over,
} from '@dnd-kit/core';
import { Box, Typography } from '@mui/material';
import { alpha } from '@mui/material/styles';
import DriveFileMoveIcon from '@mui/icons-material/DriveFileMove';
import type { ReportListItem } from 'src/hooks/useReportsApi';

/**
 * Drag-and-drop for filing reports between a space's sub-spaces.
 *
 * Two surfaces share one context, the sidebar and the contents page, and each
 * drag only targets the surface it started on, so a drop always lands where
 * the user was looking. A drag carries every report it moves: on the contents
 * page, dragging a selected row moves the whole selection.
 */

export type DndSurface = 'sidebar' | 'contents';

interface DragData {
  surface: DndSurface;
  reports: ReportListItem[];
}

interface DropData {
  surface: DndSurface;
  subspaceId: string | null;
  name: string;
}

const dragData = (active: Active | null) =>
  (active?.data.current ?? null) as DragData | null;
const dropData = (over: Over | null) =>
  (over?.data.current ?? null) as DropData | null;

function describe(reports: ReportListItem[]): string {
  return reports.length === 1
    ? `“${reports[0].name}”`
    : `${reports.length} reports`;
}

/** Collisions only against targets on the drag's own surface. */
const collisions: CollisionDetection = (args) => {
  const surface = dragData(args.active)?.surface;
  const droppableContainers = args.droppableContainers.filter(
    (container) =>
      (container.data.current as DropData | undefined)?.surface === surface,
  );
  const scoped = { ...args, droppableContainers };
  const byPointer = pointerWithin(scoped);
  return byPointer.length > 0 ? byPointer : rectIntersection(scoped);
};

/**
 * Arrow keys step to the next sub-space above or below, instead of moving the
 * dragged item a few pixels per press.
 */
const keyboardCoordinates: KeyboardCoordinateGetter = (
  event,
  { currentCoordinates, context },
) => {
  const down = event.code === 'ArrowDown' || event.code === 'ArrowRight';
  const up = event.code === 'ArrowUp' || event.code === 'ArrowLeft';
  const rect = context.collisionRect;
  if ((!down && !up) || !rect) return undefined;
  event.preventDefault();
  const surface = dragData(context.active)?.surface;
  const centre = rect.top + rect.height / 2;
  const targets = context.droppableContainers
    .getEnabled()
    .filter(
      (container) =>
        (container.data.current as DropData | undefined)?.surface === surface,
    )
    .map((container) => context.droppableRects.get(container.id))
    .filter((r): r is NonNullable<typeof r> => !!r)
    // Aim at each target's head, where its name is, not its middle.
    .map((r) => r.top + Math.min(r.height / 2, 24))
    .filter((y) => (down ? y > centre + 4 : y < centre - 4))
    .sort((a, b) => (down ? a - b : b - a));
  if (targets.length === 0) return currentCoordinates;
  return {
    x: currentCoordinates.x,
    y: currentCoordinates.y + targets[0] - centre,
  };
};

interface SpaceDndProviderProps {
  onMove: (reports: ReportListItem[], subspaceId: string | null) => void;
  /** While false, nothing can be picked up (e.g. a batch is running). */
  enabled: boolean;
  children: ReactNode;
}

export function SpaceDndProvider({
  onMove,
  enabled,
  children,
}: SpaceDndProviderProps) {
  const [dragging, setDragging] = useState<DragData | null>(null);
  const sensors = useSensors(
    // A short travel before a drag starts, so a click still opens the report.
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(KeyboardSensor, { coordinateGetter: keyboardCoordinates }),
  );

  const handleStart = (event: DragStartEvent) => {
    setDragging(dragData(event.active));
  };

  const handleEnd = (event: DragEndEvent) => {
    setDragging(null);
    const drag = dragData(event.active);
    const drop = dropData(event.over);
    if (!drag || !drop) return;
    onMove(drag.reports, drop.subspaceId);
  };

  return (
    <DndContext
      sensors={enabled ? sensors : []}
      collisionDetection={collisions}
      // Targets appear when a drag starts (the empty top level) and rows move
      // as drops land, so measure throughout rather than once at the start.
      measuring={{ droppable: { strategy: MeasuringStrategy.Always } }}
      onDragStart={handleStart}
      onDragEnd={handleEnd}
      onDragCancel={() => setDragging(null)}
      accessibility={{
        screenReaderInstructions: {
          draggable:
            'To move this report, press space or enter. Use the arrow keys to choose a sub-space, space or enter to drop, or escape to cancel.',
        },
        announcements: {
          onDragStart: ({ active }) =>
            `Picked up ${describe(dragData(active)?.reports ?? [])}.`,
          onDragOver: ({ active, over }) => {
            const drop = dropData(over);
            const what = describe(dragData(active)?.reports ?? []);
            return drop
              ? `${what} is over ${drop.name}.`
              : `${what} is not over a sub-space.`;
          },
          onDragEnd: ({ active, over }) => {
            const drop = dropData(over);
            const what = describe(dragData(active)?.reports ?? []);
            return drop
              ? `Moved ${what} to ${drop.name}.`
              : `${what} was not moved.`;
          },
          onDragCancel: ({ active }) =>
            `Cancelled. ${describe(dragData(active)?.reports ?? [])} was not moved.`,
        },
      }}
    >
      {children}
      {/* No drop animation: the row is already shown in its new place by the
          time the overlay would fly back. */}
      <DragOverlay dropAnimation={null}>
        {dragging && <DragGhost reports={dragging.reports} />}
      </DragOverlay>
    </DndContext>
  );
}

function DragGhost({ reports }: { reports: ReportListItem[] }) {
  return (
    <Box
      sx={(theme) => ({
        display: 'inline-flex',
        alignItems: 'center',
        gap: 1,
        maxWidth: 320,
        px: 1.5,
        py: 0.75,
        borderRadius: 1,
        bgcolor: 'background.paper',
        border: 1,
        borderColor: 'primary.main',
        boxShadow: `0 10px 28px ${alpha(theme.palette.common.black, 0.35)}`,
        cursor: 'grabbing',
        transform: 'rotate(-1.5deg)',
      })}
    >
      <DriveFileMoveIcon fontSize="small" color="primary" />
      <Typography variant="body2" noWrap sx={{ fontWeight: 500 }}>
        {reports.length === 1 ? reports[0].name : `${reports.length} reports`}
      </Typography>
    </Box>
  );
}

/** Make one report draggable; `reports` is everything the drag carries. */
export function useReportDrag(
  surface: DndSurface,
  report: ReportListItem,
  reports: ReportListItem[],
  disabled = false,
) {
  const { attributes, listeners, setNodeRef, setActivatorNodeRef, isDragging } =
    useDraggable({
      id: `${surface}:report:${report.report_id}`,
      data: { surface, reports } satisfies DragData,
      disabled,
    });
  return { attributes, listeners, setNodeRef, setActivatorNodeRef, isDragging };
}

/** A sub-space (null: the top level) as a drop target on one surface. */
export function useSubspaceDrop(
  surface: DndSurface,
  subspaceId: string | null,
  name: string,
) {
  const { setNodeRef, isOver } = useDroppable({
    id: `${surface}:subspace:${subspaceId ?? '__top__'}`,
    data: { surface, subspaceId, name } satisfies DropData,
  });
  const { active } = useDndContext();
  const drag = dragData(active);
  const live = drag?.surface === surface;
  // A target only invites a drop that would change something.
  const wouldMove =
    live && drag.reports.some((r) => (r.subspace_id ?? null) !== subspaceId);
  return {
    setNodeRef,
    /** A drag from this surface is in progress. */
    live,
    /** Dropping here would move at least one report. */
    wouldMove,
    /** The pointer (or keyboard) is over this target, and a drop would move. */
    isOver: isOver && wouldMove,
  };
}

/** The drop-target look for a group while a drag is live. */
export function dropTargetSx(
  live: boolean,
  wouldMove: boolean,
  isOver: boolean,
) {
  return (theme: import('@mui/material/styles').Theme) => ({
    borderRadius: 1,
    outline: '1px dashed transparent',
    outlineOffset: 2,
    transition: 'outline-color 150ms ease-out, background-color 150ms ease-out',
    ...(live &&
      wouldMove && {
        outlineColor: alpha(theme.palette.primary.main, 0.4),
      }),
    ...(isOver && {
      outlineStyle: 'solid',
      outlineColor: theme.palette.primary.main,
      bgcolor: alpha(theme.palette.primary.main, 0.08),
    }),
  });
}
