import { useMemo, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Checkbox,
  Chip,
  Drawer,
  FormControl,
  IconButton,
  InputAdornment,
  InputLabel,
  List,
  ListItem,
  ListItemButton,
  ListSubheader,
  MenuItem,
  Select,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import CloseIcon from '@mui/icons-material/Close';
import ErrorIcon from '@mui/icons-material/Error';
import SearchIcon from '@mui/icons-material/Search';
import PostAddOutlinedIcon from '@mui/icons-material/PostAddOutlined';
import ConstellationSpinner from 'src/components/ConstellationSpinner';
import { listTableTruncateSx } from 'src/components/ListTable';
import {
  notifyReportsUpdated,
  useReportsList,
  useReportsMutations,
  type ReportListItem,
} from 'src/hooks/useReportsApi';
import {
  useBulkAction,
  type BulkFailure,
  type BulkItemStatus,
} from 'src/hooks/useBulkAction';
import { useSpacesList, type SpaceTree } from 'src/hooks/useSpacesApi';
import { useCurrentUser } from 'src/hooks/useCurrentUser';

const TOP_LEVEL = '__top__';
const DRAWER_WIDTH = 480;

type Kind = 'unfiled' | 'elsewhere' | 'draft';
type Filter = 'all' | Kind;

interface Candidate {
  report: ReportListItem;
  kind: Kind;
  /** Name of the space the report would leave, for `elsewhere`. */
  fromSpace?: string;
  /** Why the report cannot be added, if it cannot. */
  blocked?: string;
}

const candidateKey = (candidate: Candidate) => candidate.report.report_id;

const SECTIONS: { kind: Kind; title: string }[] = [
  { kind: 'unfiled', title: 'Not in a space' },
  { kind: 'elsewhere', title: 'In another space' },
  { kind: 'draft', title: 'Drafts' },
];

const FILTER_LABELS: Record<Filter, string> = {
  all: 'All',
  unfiled: 'Not in a space',
  elsewhere: 'In another space',
  draft: 'Drafts',
};

export interface AddReportsDrawerProps {
  open: boolean;
  onClose: () => void;
  tree: SpaceTree;
  /** Sub-space the picker starts filing into; null for the top level. */
  initialSubspaceId?: string | null;
  /** Called after reports were filed, with how many. */
  onAdded?: (count: number, subspaceName: string | null) => void;
  /** Offered when nothing that exists fits. */
  onCreateReport?: () => void;
}

/**
 * Pick existing reports and file them into a space, from the space.
 *
 * Drafts are offered rather than hidden, because "publish it, then come back"
 * is exactly the detour this replaces; adding one publishes it, and the drawer
 * says so before the user commits. Only a draft's owner can publish it.
 */
export default function AddReportsDrawer({
  open,
  onClose,
  ...rest
}: AddReportsDrawerProps) {
  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={onClose}
      slotProps={{
        paper: {
          sx: {
            width: { xs: '100%', sm: DRAWER_WIDTH },
            display: 'flex',
            flexDirection: 'column',
          },
        },
      }}
    >
      {/* Mounted per open: the selection starts empty and the report list is
          fetched fresh each time rather than held while the drawer is shut. */}
      {open && <AddReportsPanel onClose={onClose} {...rest} />}
    </Drawer>
  );
}

function AddReportsPanel({
  onClose,
  tree,
  initialSubspaceId = null,
  onAdded,
  onCreateReport,
}: Omit<AddReportsDrawerProps, 'open'>) {
  const { reports, loading, error } = useReportsList();
  const { spaces } = useSpacesList();
  const currentUser = useCurrentUser();
  const { setReportSpace, updateReportVisibility } = useReportsMutations();

  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<Filter>('all');
  const [selected, setSelected] = useState<Set<string>>(() => new Set());
  const [target, setTarget] = useState<string>(initialSubspaceId ?? TOP_LEVEL);
  const bulk = useBulkAction<Candidate>(candidateKey);
  const [failures, setFailures] = useState<BulkFailure<Candidate>[]>([]);

  const spaceNames = useMemo(
    () => new Map(spaces.map((s) => [s.space_id, s.name])),
    [spaces],
  );

  const candidates = useMemo<Candidate[]>(() => {
    const spaceId = tree.space.space_id;
    return reports
      .filter((report) => report.space_id !== spaceId)
      .map((report): Candidate => {
        if (report.access.scope !== 'public') {
          const isOwner = currentUser?.user_id === report.created_by;
          return {
            report,
            kind: 'draft',
            blocked: isOwner
              ? undefined
              : 'Only the owner of a draft can publish it',
          };
        }
        if (report.space_id) {
          return {
            report,
            kind: 'elsewhere',
            fromSpace: spaceNames.get(report.space_id) ?? 'another space',
          };
        }
        return { report, kind: 'unfiled' };
      })
      .sort((a, b) => a.report.name.localeCompare(b.report.name));
  }, [reports, tree.space.space_id, currentUser, spaceNames]);

  const counts = useMemo(() => {
    const c: Record<Filter, number> = {
      all: candidates.length,
      unfiled: 0,
      elsewhere: 0,
      draft: 0,
    };
    candidates.forEach((candidate) => (c[candidate.kind] += 1));
    return c;
  }, [candidates]);

  const needle = query.trim().toLowerCase();
  const visible = candidates.filter(
    (candidate) =>
      (filter === 'all' || candidate.kind === filter) &&
      (!needle ||
        candidate.report.name.toLowerCase().includes(needle) ||
        (candidate.fromSpace ?? '').toLowerCase().includes(needle)),
  );

  const chosen = candidates.filter((c) => selected.has(c.report.report_id));
  const draftCount = chosen.filter((c) => c.kind === 'draft').length;
  const moving = chosen.filter((c) => c.kind === 'elsewhere');
  const busy = bulk.running;
  const progress = bulk.progress;

  const subspaceName =
    target === TOP_LEVEL
      ? null
      : (tree.subspaces.find((s) => s.subspace_id === target)?.name ?? null);

  const toggle = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const handleAdd = async () => {
    const subspaceId = target === TOP_LEVEL ? null : target;
    setFailures([]);
    // One at a time without per-item broadcasts, then one reload: the list
    // (and the space tree behind the page) refetches once for the batch, and
    // the rows hold still while their statuses fill in.
    // In display order, so progress runs down the list rather than around it.
    const batch = SECTIONS.flatMap(({ kind }) =>
      chosen.filter((c) => c.kind === kind),
    );
    const { succeeded, failed } = await bulk.run(
      batch,
      async ({ kind, report }) => {
        // A space member must be public, so a draft is published first.
        if (kind === 'draft') {
          await updateReportVisibility(report.report_id, 'public');
        }
        await setReportSpace(
          report.report_id,
          tree.space.space_id,
          subspaceId,
          {
            notify: false,
          },
        );
      },
      notifyReportsUpdated,
    );
    if (succeeded.length > 0) onAdded?.(succeeded.length, subspaceName);
    if (failed.length === 0) {
      onClose();
      return;
    }
    // Keep only what failed selected, so a retry is one click.
    setSelected(new Set(failed.map(({ item }) => item.report.report_id)));
    setFailures(failed);
  };

  const addLabel =
    chosen.length === 0
      ? 'Add reports'
      : `Add ${chosen.length} report${chosen.length === 1 ? '' : 's'}`;

  return (
    <>
      {/* Header */}
      <Box
        sx={{
          px: 3,
          pt: 2.5,
          pb: 2,
          display: 'flex',
          alignItems: 'flex-start',
          gap: 1,
          flexShrink: 0,
        }}
      >
        <Box sx={{ flexGrow: 1, minWidth: 0 }}>
          <Typography variant="h4" component="h2">
            Add reports
          </Typography>
          <Typography
            variant="body2"
            color="text.secondary"
            sx={listTableTruncateSx}
          >
            to {tree.space.name}
          </Typography>
        </Box>
        <Tooltip title="Close">
          <IconButton aria-label="Close" onClick={onClose} edge="end">
            <CloseIcon />
          </IconButton>
        </Tooltip>
      </Box>

      {/* Search and filters */}
      <Box sx={{ px: 3, pb: 1.5, flexShrink: 0 }}>
        <TextField
          autoFocus
          fullWidth
          size="small"
          placeholder="Search reports"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          slotProps={{
            htmlInput: { 'aria-label': 'Search reports' },
            input: {
              startAdornment: (
                <InputAdornment position="start">
                  <SearchIcon fontSize="small" />
                </InputAdornment>
              ),
            },
          }}
        />
        <Box
          role="group"
          aria-label="Filter reports"
          sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.75, mt: 1.5 }}
        >
          {(Object.keys(FILTER_LABELS) as Filter[])
            .filter((key) => key === 'all' || counts[key] > 0)
            .map((key) => (
              <Chip
                key={key}
                size="small"
                label={`${FILTER_LABELS[key]} ${counts[key]}`}
                color={filter === key ? 'primary' : 'default'}
                variant={filter === key ? 'filled' : 'outlined'}
                onClick={() => setFilter(key)}
                aria-pressed={filter === key}
              />
            ))}
        </Box>
      </Box>

      {/* Candidates */}
      <Box
        sx={{
          flex: 1,
          minHeight: 0,
          overflowY: 'auto',
          borderTop: 1,
          borderColor: 'divider',
        }}
      >
        {loading && candidates.length === 0 ? (
          <Box sx={{ display: 'flex', justifyContent: 'center', p: 4 }}>
            <ConstellationSpinner size={36} />
          </Box>
        ) : error ? (
          <Alert severity="error" sx={{ m: 3 }}>
            Could not load reports. {error.message}
          </Alert>
        ) : candidates.length === 0 ? (
          <EmptyMessage
            title="Every report you can see is already here"
            body="Create a new report to add to this space."
            onCreateReport={onCreateReport}
          />
        ) : visible.length === 0 ? (
          <EmptyMessage
            title="No matching reports"
            body={
              needle
                ? `Nothing outside this space matches “${query.trim()}”.`
                : 'Nothing in this filter.'
            }
            onCreateReport={onCreateReport}
          />
        ) : (
          <>
            {SECTIONS.map(({ kind, title }) => {
              const rows = visible.filter((c) => c.kind === kind);
              if (rows.length === 0) return null;
              return (
                <List
                  key={kind}
                  dense
                  disablePadding
                  subheader={
                    <ListSubheader
                      disableSticky={false}
                      sx={{
                        px: 3,
                        lineHeight: '36px',
                        bgcolor: 'background.paper',
                      }}
                    >
                      {title}
                    </ListSubheader>
                  }
                >
                  {rows.map((candidate) => (
                    <CandidateRow
                      key={candidate.report.report_id}
                      candidate={candidate}
                      checked={selected.has(candidate.report.report_id)}
                      locked={busy}
                      status={bulk.status(candidate)}
                      onToggle={() => toggle(candidate.report.report_id)}
                    />
                  ))}
                </List>
              );
            })}
            {onCreateReport && (
              <Box sx={{ px: 3, py: 2 }}>
                <Button
                  size="small"
                  startIcon={<PostAddOutlinedIcon fontSize="small" />}
                  onClick={onCreateReport}
                >
                  Create a new report instead
                </Button>
              </Box>
            )}
          </>
        )}
      </Box>

      {/* Commit */}
      <Box
        sx={{
          flexShrink: 0,
          borderTop: 1,
          borderColor: 'divider',
          px: 3,
          py: 2,
          display: 'flex',
          flexDirection: 'column',
          gap: 1.5,
          bgcolor: 'background.paper',
        }}
      >
        {failures.length > 0 && (
          <Alert severity="error">
            {failures.length === 1
              ? 'One report could not be added.'
              : `${failures.length} reports could not be added.`}
            <Box component="ul" sx={{ m: 0, pl: 2.5 }}>
              {failures.map(({ item, message }) => (
                <li key={item.report.report_id}>
                  {item.report.name}: {message}
                </li>
              ))}
            </Box>
          </Alert>
        )}
        {draftCount > 0 && (
          <Alert severity="warning" sx={{ py: 0 }}>
            {draftCount === 1
              ? '1 draft will be published.'
              : `${draftCount} drafts will be published.`}{' '}
            Reports in a space are visible to everyone.
          </Alert>
        )}
        {moving.length > 0 && (
          <Typography variant="body2" color="text.secondary">
            {moving.length === 1
              ? `“${moving[0].report.name}” moves out of ${moving[0].fromSpace}.`
              : `${moving.length} reports move out of their current space.`}
          </Typography>
        )}
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
          {tree.subspaces.length > 0 && (
            <FormControl size="small" sx={{ flex: 1, minWidth: 0 }}>
              <InputLabel id="add-reports-target">File into</InputLabel>
              <Select
                labelId="add-reports-target"
                label="File into"
                value={target}
                disabled={busy}
                onChange={(e) => setTarget(e.target.value)}
              >
                <MenuItem value={TOP_LEVEL}>Top level</MenuItem>
                {tree.subspaces.map((subspace) => (
                  <MenuItem
                    key={subspace.subspace_id}
                    value={subspace.subspace_id}
                  >
                    {subspace.name}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
          )}
          <Button
            variant="contained"
            onClick={handleAdd}
            disabled={busy || chosen.length === 0}
            sx={{
              flexShrink: 0,
              whiteSpace: 'nowrap',
              ml: tree.subspaces.length > 0 ? 0 : 'auto',
              minWidth: 148,
            }}
          >
            {/* The rows carry the spinner; the button carries the count. */}
            {progress
              ? `Adding ${Math.min(progress.done + 1, progress.of)} of ${progress.of}`
              : addLabel}
          </Button>
        </Box>
      </Box>
    </>
  );
}

function CandidateRow({
  candidate,
  checked,
  locked,
  status,
  onToggle,
}: {
  candidate: Candidate;
  checked: boolean;
  /** A batch is running: nothing toggles, and only rows outside it dim. */
  locked: boolean;
  status: BulkItemStatus | undefined;
  onToggle: () => void;
}) {
  const { report, kind, fromSpace, blocked } = candidate;
  const labelId = `add-report-${report.report_id}`;
  const detail =
    blocked ??
    (kind === 'elsewhere'
      ? `In ${fromSpace} — moves here`
      : kind === 'draft'
        ? 'Draft — published when added'
        : `Updated ${new Date(report.updated_at).toLocaleDateString()}`);

  return (
    <ListItem disablePadding>
      <ListItemButton
        onClick={locked ? undefined : onToggle}
        // Rows outside the batch dim; rows in it stay readable so their
        // status can be followed, but cannot be toggled mid-run.
        disabled={!!blocked || (locked && !checked)}
        aria-disabled={locked || !!blocked || undefined}
        sx={{
          px: 3,
          py: 0.75,
          gap: 1.5,
          alignItems: 'center',
          ...(locked && checked && { cursor: 'default' }),
        }}
      >
        <Checkbox
          edge="start"
          checked={checked}
          tabIndex={-1}
          disableRipple
          size="small"
          slotProps={{ input: { 'aria-labelledby': labelId } }}
          sx={{ p: 0.5, ml: -0.5 }}
        />
        <Box sx={{ minWidth: 0, flex: 1 }}>
          <Typography id={labelId} variant="body2" sx={listTableTruncateSx}>
            {report.name}
          </Typography>
          <Typography
            variant="caption"
            sx={{
              ...listTableTruncateSx,
              display: 'block',
              color:
                kind === 'draft' && !blocked
                  ? 'warning.main'
                  : 'text.secondary',
            }}
          >
            {detail}
          </Typography>
        </Box>
        <RowStatus status={status} />
      </ListItemButton>
    </ListItem>
  );
}

/** Where one row stands in the batch; empty outside one. */
function RowStatus({ status }: { status: BulkItemStatus | undefined }) {
  return (
    <Box
      aria-live="polite"
      sx={{
        width: 20,
        height: 20,
        flexShrink: 0,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
      }}
    >
      {status === 'running' && <ConstellationSpinner size={18} />}
      {status === 'done' && (
        <CheckCircleIcon
          color="success"
          sx={{ fontSize: 18 }}
          titleAccess="Added"
        />
      )}
      {status === 'failed' && (
        <ErrorIcon
          color="error"
          sx={{ fontSize: 18 }}
          titleAccess="Could not be added"
        />
      )}
    </Box>
  );
}

function EmptyMessage({
  title,
  body,
  onCreateReport,
}: {
  title: string;
  body: string;
  onCreateReport?: () => void;
}) {
  return (
    <Box sx={{ px: 3, py: 5, textAlign: 'center' }}>
      <Typography variant="h6" component="p" sx={{ mb: 0.5 }}>
        {title}
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        {body}
      </Typography>
      {onCreateReport && (
        <Button
          variant="outlined"
          startIcon={<PostAddOutlinedIcon />}
          onClick={onCreateReport}
        >
          New report
        </Button>
      )}
    </Box>
  );
}
