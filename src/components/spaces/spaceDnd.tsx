import { useState, type ReactNode } from 'react';
import {
  closestCenter,
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
import { sortableKeyboardCoordinates, useSortable } from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import { Box, Typography } from '@mui/material';
import { alpha } from '@mui/material/styles';
import DriveFileMoveIcon from '@mui/icons-material/DriveFileMove';
import type { ReportListItem } from 'src/hooks/useReportsApi';
import type { SubspaceItem } from 'src/hooks/useSpacesApi';

/**
 * Drag-and-drop within a space: filing reports between sub-spaces, and
 * reordering the sub-spaces themselves.
 *
 * Two surfaces share one context, the sidebar and the contents page, and each
 * drag only targets the surface it started on, so a drop always lands where
 * the user was looking. A report drag carries every report it moves: on the
 * contents page, dragging a selected row moves the whole selection. Sub-spaces
 * are reordered on the contents page only.
 */

export type DndSurface = 'sidebar' | 'contents';

/** Dragging reports, to file them somewhere else. */
interface ReportDragData {
  kind: 'reports';
  surface: DndSurface;
  reports: ReportListItem[];
}

/** Dragging a sub-space, to reorder it. */
interface SubspaceDragData {
  kind: 'subspace';
  surface: DndSurface;
  subspace: SubspaceItem;
}

type DragData = ReportDragData | SubspaceDragData;

/** A sub-space (or the top level) accepting reports. */
interface ReportDropData {
  kind: 'report-drop';
  surface: DndSurface;
  subspaceId: string | null;
  name: string;
}

/**
 * A sub-space's slot in the order. `useSortable` gives its draggable and its
 * droppable one data object, so a slot carries the drag's own shape.
 */
type DropData = ReportDropData | SubspaceDragData;

const dragData = (active: Active | null) =>
  (active?.data.current ?? null) as DragData | null;
const dropData = (over: Over | null) =>
  (over?.data.current ?? null) as DropData | null;

/** The drop kind a drag of `drag` can land on. */
const targetKind = (drag: DragData | null): DropData['kind'] =>
  drag?.kind === 'subspace' ? 'subspace' : 'report-drop';

const sortId = (surface: DndSurface, subspaceId: string) =>
  `${surface}:sort:${subspaceId}`;

function describeDrag(drag: DragData | null): string {
  if (!drag) return 'Nothing';
  return drag.kind === 'subspace'
    ? `“${drag.subspace.name}”`
    : describe(drag.reports);
}

function describe(reports: ReportListItem[]): string {
  return reports.length === 1
    ? `“${reports[0].name}”`
    : `${reports.length} reports`;
}

/** Collisions only against targets of the drag's kind, on its own surface. */
const collisions: CollisionDetection = (args) => {
  const drag = dragData(args.active);
  const kind = targetKind(drag);
  const droppableContainers = args.droppableContainers.filter((container) => {
    const data = container.data.current as DropData | undefined;
    return data?.surface === drag?.surface && data?.kind === kind;
  });
  const scoped = { ...args, droppableContainers };
  // A reorder takes the nearest slot, as the panel editor's rows do.
  if (kind === 'subspace') return closestCenter(scoped);
  // A filing lands on the group the pointer is actually inside.
  const byPointer = pointerWithin(scoped);
  return byPointer.length > 0 ? byPointer : rectIntersection(scoped);
};

/**
 * Arrow keys step to the next sub-space above or below, instead of moving the
 * dragged item a few pixels per press.
 */
const keyboardCoordinates: KeyboardCoordinateGetter = (event, args) => {
  const { currentCoordinates, context } = args;
  const drag = dragData(context.active);
  // A sub-space steps slot to slot; the targets of a report drag are disabled
  // meanwhile, so only slots are candidates.
  if (drag?.kind === 'subspace')
    return sortableKeyboardCoordinates(event, args);
  const down = event.code === 'ArrowDown' || event.code === 'ArrowRight';
  const up = event.code === 'ArrowUp' || event.code === 'ArrowLeft';
  const rect = context.collisionRect;
  if ((!down && !up) || !rect) return undefined;
  event.preventDefault();
  const centre = rect.top + rect.height / 2;
  const targets = context.droppableContainers
    .getEnabled()
    .filter((container) => {
      const data = container.data.current as DropData | undefined;
      return data?.surface === drag?.surface && data?.kind === 'report-drop';
    })
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
  /** Put `subspaceId` where `overSubspaceId` is, shifting the rest. */
  onReorder: (subspaceId: string, overSubspaceId: string) => void;
  /** While false, nothing can be picked up (e.g. a batch is running). */
  enabled: boolean;
  children: ReactNode;
}

export function SpaceDndProvider({
  onMove,
  onReorder,
  enabled,
  children,
}: SpaceDndProviderProps) {
  const [dragging, setDragging] = useState<DragData | null>(null);
  const sensors = useSensors(
    // A short travel before a drag starts, so a click still opens the report.
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(KeyboardSensor, { coordinateGetter: keyboardCoordinates }),
  );

  const finish = () => setDragging(null);

  const handleStart = (event: DragStartEvent) => {
    setDragging(dragData(event.active));
  };

  const handleEnd = (event: DragEndEvent) => {
    finish();
    const drag = dragData(event.active);
    const drop = dropData(event.over);
    if (!drag || !drop) return;
    if (drag.kind === 'subspace') {
      if (
        drop.kind === 'subspace' &&
        drop.subspace.subspace_id !== drag.subspace.subspace_id
      ) {
        onReorder(drag.subspace.subspace_id, drop.subspace.subspace_id);
      }
      return;
    }
    if (drop.kind === 'report-drop') onMove(drag.reports, drop.subspaceId);
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
      onDragCancel={finish}
      accessibility={{
        screenReaderInstructions: {
          draggable:
            'To move this item, press space or enter. Use the arrow keys to choose where it goes, space or enter to drop, or escape to cancel.',
        },
        announcements: {
          onDragStart: ({ active }) =>
            `Picked up ${describeDrag(dragData(active))}.`,
          onDragOver: ({ active, over }) => {
            const drag = dragData(active);
            const drop = dropData(over);
            const what = describeDrag(drag);
            if (drag?.kind === 'subspace') {
              return drop?.kind === 'subspace'
                ? `${what} is in the place of ${drop.subspace.name}.`
                : `${what} is not over a sub-space.`;
            }
            return drop?.kind === 'report-drop'
              ? `${what} is over ${drop.name}.`
              : `${what} is not over a sub-space.`;
          },
          onDragEnd: ({ active, over }) => {
            const drag = dragData(active);
            const drop = dropData(over);
            const what = describeDrag(drag);
            if (drag?.kind === 'subspace') {
              return drop?.kind === 'subspace' &&
                drop.subspace.subspace_id !== drag.subspace.subspace_id
                ? `Moved ${what} to the place of ${drop.subspace.name}.`
                : `${what} was not moved.`;
            }
            return drop?.kind === 'report-drop'
              ? `Moved ${what} to ${drop.name}.`
              : `${what} was not moved.`;
          },
          onDragCancel: ({ active }) =>
            `Cancelled. ${describeDrag(dragData(active))} was not moved.`,
        },
      }}
    >
      {children}
      {/* Reports only: a dragged sub-space moves itself while the others
          slide aside, so there is no ghost to follow. No drop animation: the
          row is already shown in its new place by the time it would fly back. */}
      <DragOverlay dropAnimation={null}>
        {dragging?.kind === 'reports' && (
          <DragGhost reports={dragging.reports} />
        )}
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
      data: { kind: 'reports', surface, reports } satisfies ReportDragData,
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
  const { active } = useDndContext();
  const activeDrag = dragData(active);
  const { setNodeRef, isOver } = useDroppable({
    id: `${surface}:subspace:${subspaceId ?? '__top__'}`,
    data: {
      kind: 'report-drop',
      surface,
      subspaceId,
      name,
    } satisfies ReportDropData,
    // Out of the way of a reorder, keyboard stepping included.
    disabled: activeDrag?.kind === 'subspace',
  });
  const drag = activeDrag?.kind === 'reports' ? activeDrag : null;
  const live = drag?.surface === surface;
  // A target only invites a drop that would change something.
  const wouldMove =
    !!drag &&
    live &&
    drag.reports.some((r) => (r.subspace_id ?? null) !== subspaceId);
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

/**
 * Make a sub-space reorderable on `surface`. Render the list inside a
 * `SortableContext` whose items are `subspaceSortIds(surface, subspaces)`.
 */
export function useSubspaceSort(
  surface: DndSurface,
  subspace: SubspaceItem,
  disabled = false,
) {
  const { active } = useDndContext();
  const reordering = dragData(active)?.kind === 'subspace';
  const {
    attributes,
    listeners,
    setNodeRef,
    setActivatorNodeRef,
    isDragging,
    transform,
    transition,
  } = useSortable({
    id: sortId(surface, subspace.subspace_id),
    data: { kind: 'subspace', surface, subspace } satisfies SubspaceDragData,
    // Slots only exist during a reorder, so a report drag never sees them.
    disabled: { draggable: disabled, droppable: !reordering },
  });
  return {
    attributes,
    listeners,
    setNodeRef,
    setActivatorNodeRef,
    isDragging,
    /**
     * The panel's own movement: the dragged one follows the pointer and the
     * rest slide aside. Translate only, since panels differ in height.
     */
    style: { transform: CSS.Translate.toString(transform), transition },
  };
}

export const subspaceSortIds = (
  surface: DndSurface,
  subspaces: SubspaceItem[],
) => subspaces.map((s) => sortId(surface, s.subspace_id));

/** Reports are being dragged to file them, on any surface. */
export function useFiling(): boolean {
  const { active } = useDndContext();
  return dragData(active)?.kind === 'reports';
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
