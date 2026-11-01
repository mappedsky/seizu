import { useMemo, type PointerEventHandler } from 'react';
import {
  Box,
  Button,
  Divider,
  IconButton,
  List,
  ListItem,
  ListItemButton,
  ListItemIcon,
  ListSubheader,
  Tooltip,
  Typography,
} from '@mui/material';
import ChevronLeft from '@mui/icons-material/ChevronLeft';
import ChevronRight from '@mui/icons-material/ChevronRight';
import CreateNewFolderOutlinedIcon from '@mui/icons-material/CreateNewFolderOutlined';
import LibraryAddOutlinedIcon from '@mui/icons-material/LibraryAddOutlined';
import PostAddOutlinedIcon from '@mui/icons-material/PostAddOutlined';
import StarIcon from '@mui/icons-material/Star';
import SidePanelFrame from 'src/components/SidePanelFrame';
import RowMenu from 'src/components/RowMenu';
import { listTableTruncateSx } from 'src/components/ListTable';
import SpaceConstellation from 'src/components/spaces/SpaceConstellation';
import {
  dropTargetSx,
  useReportDrag,
  useSubspaceDrop,
} from 'src/components/spaces/spaceDnd';
import type { SpaceManagement } from 'src/components/spaces/useSpaceManagement';
import type { ReportListItem } from 'src/hooks/useReportsApi';
import type { SpaceTree, SubspaceItem } from 'src/hooks/useSpacesApi';

// Row actions sit in a fixed column at the panel's right edge: a slot reserved
// on every row for the overview star, then the row menu, so the menus stay in
// one line whether or not a row is the overview.
const ROW_MENU_WIDTH = 30;
const OVERVIEW_STAR_SIZE = 16;
const OVERVIEW_STAR_SLOT = OVERVIEW_STAR_SIZE + 2;
const ACTION_COLUMN_WIDTH = ROW_MENU_WIDTH + OVERVIEW_STAR_SLOT;
const REPORT_NAME_GAP = 2;

interface SpaceReportsPanelProps {
  open: boolean;
  /** Float over the report instead of beside it (see `useSidePanel`). */
  overlay?: boolean;
  onToggle: () => void;
  tree: SpaceTree;
  /** The report whose row is highlighted — the URL's report, if any. */
  selectedReportId: string | undefined;
  /** Whether the space's own contents page is on screen. */
  contentsSelected: boolean;
  onSelectReport: (reportId: string) => void;
  onOpenContents: () => void;
  manage: SpaceManagement;
}

interface ReportGroup {
  subspace: SubspaceItem | null;
  reports: ReportListItem[];
}

function SpaceReportsPanel({
  open,
  overlay = false,
  onToggle,
  tree,
  selectedReportId,
  contentsSelected,
  onSelectReport,
  onOpenContents,
  manage,
}: SpaceReportsPanelProps) {
  const groups = useMemo<ReportGroup[]>(() => {
    const members = tree.reports;
    // The API already blanks out any subspace_id left behind by a deleted
    // sub-space, so anything unmatched here is genuinely top level.
    return [
      { subspace: null, reports: members.filter((r) => !r.subspace_id) },
      // Empty sub-spaces still render, so they stay renameable and fillable.
      ...tree.subspaces.map((subspace) => ({
        subspace,
        reports: members.filter((r) => r.subspace_id === subspace.subspace_id),
      })),
    ];
  }, [tree]);

  const showReportMenu = manage.canWriteReports || manage.canWriteSpaces;

  if (!open) {
    return (
      <SidePanelFrame open={false} overlay={overlay} onClose={onToggle}>
        <Box sx={{ display: 'flex', justifyContent: 'center', pt: 1 }}>
          <Tooltip title="Show reports">
            <IconButton
              aria-label="Show reports"
              size="small"
              onClick={onToggle}
            >
              <ChevronRight />
            </IconButton>
          </Tooltip>
        </Box>
      </SidePanelFrame>
    );
  }

  // The frame clips, so the scroll region is the list below rather than the
  // whole panel, and the header stays put instead of scrolling out from under
  // the navbar.
  return (
    <SidePanelFrame open overlay={overlay} onClose={onToggle}>
      <Box
        sx={{
          alignItems: 'center',
          display: 'flex',
          flexShrink: 0,
          gap: 0.5,
          pl: 1,
          pr: 1,
          pt: 1.5,
          pb: 1,
        }}
      >
        <ListItemButton
          selected={contentsSelected}
          onClick={onOpenContents}
          aria-label={`${tree.space.name} contents`}
          sx={{ borderRadius: 1, gap: 1.25, px: 1, py: 0.75, minWidth: 0 }}
        >
          <SpaceConstellation
            seed={tree.space.space_id}
            groups={tree.reports.map((r) => r.subspace_id)}
            size={28}
          />
          <Box sx={{ minWidth: 0 }}>
            <Tooltip
              title={tree.space.name}
              placement="top"
              arrow
              disableInteractive
            >
              <Typography
                variant="subtitle2"
                sx={{
                  ...listTableTruncateSx,
                  fontWeight: 600,
                  lineHeight: 1.3,
                }}
              >
                {tree.space.name}
              </Typography>
            </Tooltip>
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ display: 'block', lineHeight: 1.3 }}
            >
              Contents
            </Typography>
          </Box>
        </ListItemButton>
        <Tooltip title="Hide reports">
          <IconButton aria-label="Hide reports" size="small" onClick={onToggle}>
            <ChevronLeft />
          </IconButton>
        </Tooltip>
      </Box>
      <Divider />

      {/* Only the list scrolls. The header and footer must stay outside this
          box, or the header scrolls up under the fixed navbar. */}
      <Box
        data-testid="space-reports-scroll"
        sx={{ flex: 1, minHeight: 0, overflowY: 'auto', pt: 0.5 }}
      >
        {tree.reports.length === 0 && tree.subspaces.length === 0 && (
          <Typography
            variant="body2"
            color="text.secondary"
            sx={{ px: 2, py: 1.5 }}
          >
            No reports yet.
          </Typography>
        )}
        {groups.map((group) => (
          <SidebarGroup
            key={group.subspace?.subspace_id ?? '__top__'}
            group={group}
            hasSubspaces={tree.subspaces.length > 0}
            overviewId={tree.space.overview_report_id}
            selectedReportId={selectedReportId}
            onSelectReport={onSelectReport}
            showReportMenu={showReportMenu}
            manage={manage}
          />
        ))}
      </Box>

      {(manage.canWriteReports || manage.canWriteSpaces) && (
        <Box
          sx={{
            borderTop: 1,
            borderColor: 'divider',
            flexShrink: 0,
            p: 1.5,
            display: 'flex',
            flexDirection: 'column',
            gap: 0.5,
          }}
        >
          {manage.canWriteReports && (
            <Button
              fullWidth
              variant="contained"
              startIcon={<LibraryAddOutlinedIcon />}
              onClick={() => manage.openAddReports()}
            >
              Add reports
            </Button>
          )}
          <List dense disablePadding>
            {manage.canWriteReports && (
              <ListItem disablePadding>
                <ListItemButton
                  onClick={() => manage.openCreateReport()}
                  sx={{ borderRadius: 1 }}
                >
                  <ListItemIcon sx={{ minWidth: 32 }}>
                    <PostAddOutlinedIcon fontSize="small" />
                  </ListItemIcon>
                  <Typography variant="body2">New report</Typography>
                </ListItemButton>
              </ListItem>
            )}
            {manage.canWriteSpaces && (
              <ListItem disablePadding>
                <ListItemButton
                  onClick={manage.openCreateSubspace}
                  sx={{ borderRadius: 1 }}
                >
                  <ListItemIcon sx={{ minWidth: 32 }}>
                    <CreateNewFolderOutlinedIcon fontSize="small" />
                  </ListItemIcon>
                  <Typography variant="body2">New sub-space</Typography>
                </ListItemButton>
              </ListItem>
            )}
          </List>
        </Box>
      )}
    </SidePanelFrame>
  );
}

interface SidebarGroupProps {
  group: ReportGroup;
  hasSubspaces: boolean;
  overviewId: string | null;
  selectedReportId: string | undefined;
  onSelectReport: (reportId: string) => void;
  showReportMenu: boolean;
  manage: SpaceManagement;
}

/** One sub-space (or the top level): a heading, its reports, a drop target. */
function SidebarGroup({
  group,
  hasSubspaces,
  overviewId,
  selectedReportId,
  onSelectReport,
  showReportMenu,
  manage,
}: SidebarGroupProps) {
  const { subspace, reports } = group;
  const drop = useSubspaceDrop(
    'sidebar',
    subspace?.subspace_id ?? null,
    subspace?.name ?? 'the top level',
  );
  // The top level has no heading of its own, and is hidden when empty, except
  // while a drag needs it as a target.
  if (!subspace && reports.length === 0 && !drop.live) return null;
  const heading =
    subspace?.name ?? (drop.live && hasSubspaces ? 'Top level' : null);

  return (
    <Box
      ref={drop.setNodeRef}
      sx={[dropTargetSx(drop.live, drop.wouldMove, drop.isOver), { mx: 0.5 }]}
    >
      <List
        dense
        disablePadding
        subheader={
          heading ? (
            <ListSubheader
              sx={{
                display: 'flex',
                alignItems: 'center',
                gap: 0.5,
                lineHeight: '36px',
                pr: 0,
                pl: 1.5,
                // Transparent while it is a drop target, so the target's
                // highlight shows through the heading too.
                bgcolor: drop.live ? 'transparent' : undefined,
              }}
            >
              <Tooltip title={heading} placement="top" arrow disableInteractive>
                <Box
                  component="span"
                  sx={{ flexGrow: 1, ...listTableTruncateSx }}
                >
                  {heading}
                </Box>
              </Tooltip>
              {subspace &&
                (manage.canWriteSpaces || manage.canWriteReports) && (
                  <RowMenu
                    actions={manage.subspaceActions(subspace)}
                    label="Sub-space actions"
                    menuMinWidth={200}
                  />
                )}
            </ListSubheader>
          ) : undefined
        }
      >
        {reports.length === 0 && (
          <ListItem sx={{ pl: 1.5 }}>
            <Typography variant="caption" color="text.secondary">
              {drop.isOver ? 'Drop to move here' : 'No reports'}
            </Typography>
          </ListItem>
        )}
        {reports.map((report) => (
          <SidebarReport
            key={report.report_id}
            report={report}
            isOverview={report.report_id === overviewId}
            selected={selectedReportId === report.report_id}
            onSelect={() => onSelectReport(report.report_id)}
            showMenu={showReportMenu}
            manage={manage}
          />
        ))}
      </List>
    </Box>
  );
}

function SidebarReport({
  report,
  isOverview,
  selected,
  onSelect,
  showMenu,
  manage,
}: {
  report: ReportListItem;
  isOverview: boolean;
  selected: boolean;
  onSelect: () => void;
  showMenu: boolean;
  manage: SpaceManagement;
}) {
  const drag = useReportDrag(
    'sidebar',
    report,
    [report],
    !manage.canWriteReports || manage.moving,
  );
  // Pointer only: in the sidebar Enter and Space open the report, so the
  // keyboard path to a move is the row menu's Move to items.
  const onPointerDown = drag.listeners?.onPointerDown as
    | PointerEventHandler<HTMLDivElement>
    | undefined;

  return (
    <ListItem
      ref={drag.setNodeRef}
      disablePadding
      sx={{
        pr: `${ACTION_COLUMN_WIDTH + REPORT_NAME_GAP}px`,
        opacity: drag.isDragging ? 0.4 : 1,
        '& .MuiListItemSecondaryAction-root': { right: 0 },
        // With secondaryAction set, ListItem pads its button child by another
        // 48px through a selector that outranks sx on the button; the row
        // padding above already reserves it.
        '& > .MuiListItemButton-root': { pr: 0 },
      }}
      secondaryAction={
        <Box sx={{ alignItems: 'center', display: 'flex' }}>
          <Box
            sx={{
              alignItems: 'center',
              display: 'flex',
              flexShrink: 0,
              justifyContent: 'flex-end',
              width: `${OVERVIEW_STAR_SLOT}px`,
            }}
          >
            {isOverview && (
              <StarIcon
                color="secondary"
                titleAccess="Space overview"
                sx={{ fontSize: OVERVIEW_STAR_SIZE }}
              />
            )}
          </Box>
          {showMenu && (
            <RowMenu
              actions={manage.reportActions(report)}
              label="Report actions"
              menuMinWidth={200}
            />
          )}
        </Box>
      }
    >
      <ListItemButton
        ref={drag.setActivatorNodeRef}
        // Highlight tracks the report the user picked, not the overview
        // fallback: the star is what marks the overview.
        selected={selected}
        onClick={onSelect}
        onPointerDown={onPointerDown}
        sx={{ pl: 1.5, borderRadius: 1, touchAction: 'pan-y' }}
      >
        <Tooltip title={report.name} placement="top" arrow disableInteractive>
          <Typography
            variant="body2"
            sx={{ ...listTableTruncateSx, width: '100%' }}
          >
            {report.name}
          </Typography>
        </Tooltip>
      </ListItemButton>
    </ListItem>
  );
}

export default SpaceReportsPanel;
