import { useMemo, useState } from 'react';
import PageTitle from 'src/components/PageTitle';
import { Link as RouterLink, useNavigate } from 'react-router-dom';
import {
  Box,
  Button,
  InputAdornment,
  Link,
  TextField,
  Typography,
} from '@mui/material';
import { alpha } from '@mui/material/styles';
import AddIcon from '@mui/icons-material/Add';
import DeleteIcon from '@mui/icons-material/Delete';
import EditIcon from '@mui/icons-material/Edit';
import OpenInNewIcon from '@mui/icons-material/OpenInNew';
import SearchIcon from '@mui/icons-material/Search';
import ListPageHeader from 'src/components/ListPageHeader';
import ListViewState from 'src/components/ListViewState';
import { listTableTruncateSx } from 'src/components/ListTable';
import RowMenu, { RowMenuAction } from 'src/components/RowMenu';
import ConfirmDeleteDialog from 'src/components/ConfirmDeleteDialog';
import UserDisplay from 'src/components/UserDisplay';
import SpaceConstellation from 'src/components/spaces/SpaceConstellation';
import SpaceDialog from 'src/components/spaces/SpaceDialog';
import {
  useSpacesList,
  useSpaceMutations,
  SpaceListItem,
} from 'src/hooks/useSpacesApi';
import { useReportsList, type ReportListItem } from 'src/hooks/useReportsApi';
import { usePermissions } from 'src/hooks/usePermissions';
import { pageContentSx } from 'src/theme/layout';

/** Member reports named on a space's row before the rest are counted. */
const PREVIEW_REPORTS = 4;
/** Below this many spaces a search field is noise. */
const SEARCH_THRESHOLD = 6;

function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? '' : 's'}`;
}

function Spaces() {
  const navigate = useNavigate();
  const hasPermission = usePermissions();
  const { spaces, loading, error } = useSpacesList();
  const { reports } = useReportsList();
  const { createSpace, updateSpace, deleteSpace } = useSpaceMutations();

  const [query, setQuery] = useState('');
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<SpaceListItem | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<SpaceListItem | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const canWrite = hasPermission('spaces:write');
  const canDelete = hasPermission('spaces:delete');

  // Membership from the report list: one fetch for every row, instead of a
  // tree request per space.
  const membersBySpace = useMemo(() => {
    const map = new Map<string, ReportListItem[]>();
    reports.forEach((report) => {
      if (!report.space_id) return;
      map.set(report.space_id, [...(map.get(report.space_id) ?? []), report]);
    });
    map.forEach((list) =>
      list.sort((a, b) => b.updated_at.localeCompare(a.updated_at)),
    );
    return map;
  }, [reports]);

  const needle = query.trim().toLowerCase();
  const shown = useMemo(
    () =>
      [...spaces]
        .sort((a, b) => a.name.localeCompare(b.name))
        .filter(
          (space) =>
            !needle ||
            space.name.toLowerCase().includes(needle) ||
            space.description.toLowerCase().includes(needle) ||
            (membersBySpace.get(space.space_id) ?? []).some((r) =>
              r.name.toLowerCase().includes(needle),
            ),
        ),
    [spaces, needle, membersBySpace],
  );

  const openCreate = () => {
    setEditTarget(null);
    setDialogOpen(true);
  };

  const handleSave = async (name: string, description: string) => {
    if (editTarget) {
      await updateSpace(editTarget.space_id, name, description);
    } else {
      const created = await createSpace(name, description);
      navigate(`/app/spaces/${created.space_id}`);
    }
  };

  const handleDeleteConfirm = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      await deleteSpace(deleteTarget.space_id);
      setDeleteTarget(null);
    } catch (err) {
      // A non-empty space returns 409 with an actionable message.
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      setDeleteError((err as any)?.message ?? 'Failed to delete space.');
    } finally {
      setDeleting(false);
    }
  };

  const rowActions = (space: SpaceListItem): RowMenuAction[] => {
    const memberCount = membersBySpace.get(space.space_id)?.length ?? 0;
    return [
      {
        key: 'open',
        label: 'Open',
        icon: <OpenInNewIcon fontSize="small" />,
        onClick: () => navigate(`/app/spaces/${space.space_id}`),
      },
      {
        key: 'edit',
        label: 'Edit',
        icon: <EditIcon fontSize="small" />,
        onClick: () => {
          setEditTarget(space);
          setDialogOpen(true);
        },
        disabled: !canWrite,
        tooltip: canWrite
          ? undefined
          : 'You do not have permission to edit spaces',
      },
      {
        key: 'delete',
        label: 'Delete',
        icon: <DeleteIcon fontSize="small" />,
        onClick: () => {
          setDeleteError(null);
          setDeleteTarget(space);
        },
        disabled: !canDelete || memberCount > 0,
        tooltip: !canDelete
          ? 'You do not have permission to delete spaces'
          : memberCount > 0
            ? `Move its ${plural(memberCount, 'report')} out first`
            : undefined,
        destructive: true,
        dividerBefore: true,
      },
    ];
  };

  return (
    <>
      <PageTitle>Spaces | Seizu</PageTitle>
      <Box sx={pageContentSx}>
        <ListPageHeader
          title={
            <Box>
              <Typography variant="h1">Spaces</Typography>
              <Typography color="text.secondary" sx={{ mt: 0.5 }}>
                Reports gathered by team, environment or question.
              </Typography>
            </Box>
          }
          action={
            canWrite && (
              <Button
                variant="contained"
                startIcon={<AddIcon />}
                onClick={openCreate}
              >
                New space
              </Button>
            )
          }
        />

        <ListViewState
          loading={loading}
          error={error}
          errorMessage="Failed to load spaces"
        >
          {spaces.length === 0 ? (
            <EmptyIndex canWrite={canWrite} onCreate={openCreate} />
          ) : (
            <>
              {spaces.length >= SEARCH_THRESHOLD && (
                <TextField
                  size="small"
                  placeholder="Search spaces and their reports"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  sx={{ mb: 2, width: { xs: '100%', sm: 360 } }}
                  slotProps={{
                    htmlInput: { 'aria-label': 'Search spaces' },
                    input: {
                      startAdornment: (
                        <InputAdornment position="start">
                          <SearchIcon fontSize="small" />
                        </InputAdornment>
                      ),
                    },
                  }}
                />
              )}
              {shown.length === 0 ? (
                <Typography color="text.secondary" sx={{ py: 3 }}>
                  No space or report matches “{query.trim()}”.
                </Typography>
              ) : (
                <Box
                  component="ul"
                  sx={{
                    listStyle: 'none',
                    m: 0,
                    p: 0,
                    borderTop: 1,
                    borderColor: 'divider',
                  }}
                >
                  {shown.map((space) => (
                    <SpaceRow
                      key={space.space_id}
                      space={space}
                      members={membersBySpace.get(space.space_id) ?? []}
                      actions={rowActions(space)}
                    />
                  ))}
                </Box>
              )}
            </>
          )}
        </ListViewState>
      </Box>

      <SpaceDialog
        key={editTarget?.space_id ?? 'new'}
        open={dialogOpen}
        onClose={() => setDialogOpen(false)}
        onSave={handleSave}
        initial={editTarget}
      />

      <ConfirmDeleteDialog
        open={!!deleteTarget}
        title="Delete space?"
        deleting={deleting}
        error={deleteError}
        onClose={() => setDeleteTarget(null)}
        onConfirm={handleDeleteConfirm}
      >
        Permanently delete <strong>{deleteTarget?.name}</strong> and its
        sub-spaces? Every report must be moved out first; no report is ever
        deleted with a space. This cannot be undone.
      </ConfirmDeleteDialog>
    </>
  );
}

function SpaceRow({
  space,
  members,
  actions,
}: {
  space: SpaceListItem;
  members: ReportListItem[];
  actions: RowMenuAction[];
}) {
  const href = `/app/spaces/${space.space_id}`;
  const preview = members.slice(0, PREVIEW_REPORTS);
  const rest = members.length - preview.length;

  return (
    <Box
      component="li"
      sx={(theme) => ({
        position: 'relative',
        display: 'grid',
        gridTemplateColumns: 'auto minmax(0, 1fr) auto',
        columnGap: { xs: 2, sm: 2.5 },
        alignItems: 'start',
        py: 2.5,
        pl: { xs: 1, sm: 1.5 },
        pr: 0.5,
        borderBottom: 1,
        borderColor: 'divider',
        transition: 'background-color 150ms ease-out',
        '&:hover, &:focus-within': {
          bgcolor: alpha(theme.palette.primary.main, 0.05),
        },
        '&:hover .space-orbit': { transform: 'rotate(18deg)' },
      })}
    >
      <Box
        className="space-orbit"
        sx={{
          transition: 'transform 600ms cubic-bezier(0.16, 1, 0.3, 1)',
          '@media (prefers-reduced-motion: reduce)': { transition: 'none' },
        }}
      >
        <SpaceConstellation
          seed={space.space_id}
          groups={members.map((r) => r.subspace_id)}
          size={{ xs: 40, sm: 56 }}
        />
      </Box>

      <Box sx={{ minWidth: 0 }}>
        <Link
          component={RouterLink}
          to={href}
          underline="hover"
          color="text.primary"
          variant="h4"
          sx={{
            display: 'inline-block',
            maxWidth: '100%',
            ...listTableTruncateSx,
            // The whole row is the target; the report links sit above it.
            '&::after': { content: '""', position: 'absolute', inset: 0 },
          }}
        >
          {space.name}
        </Link>
        {space.description && (
          <Typography
            color="text.secondary"
            sx={{ mt: 0.25, maxWidth: '72ch' }}
          >
            {space.description}
          </Typography>
        )}
        <Typography
          variant="body2"
          color="text.secondary"
          sx={{
            mt: 0.75,
            display: 'flex',
            flexWrap: 'wrap',
            columnGap: 1,
            fontVariantNumeric: 'tabular-nums',
          }}
        >
          <span>{plural(members.length, 'report')}</span>
          <span aria-hidden>·</span>
          <span>
            Updated {new Date(space.updated_at).toLocaleDateString()} by{' '}
            <UserDisplay userId={space.updated_by ?? space.created_by} />
          </span>
        </Typography>

        {preview.length > 0 && (
          <Box
            component="ul"
            aria-label={`Reports in ${space.name}`}
            sx={{
              listStyle: 'none',
              m: 0,
              p: 0,
              mt: 1.5,
              display: 'flex',
              flexWrap: 'wrap',
              gap: 0.75,
              position: 'relative',
              zIndex: 1,
              // Let clicks between chips fall through to the row link.
              pointerEvents: 'none',
            }}
          >
            {preview.map((report) => (
              <Box component="li" key={report.report_id} sx={{ minWidth: 0 }}>
                <Link
                  component={RouterLink}
                  to={`${href}/reports/${report.report_id}`}
                  underline="none"
                  sx={(theme) => ({
                    typography: 'body2',
                    pointerEvents: 'auto',
                    display: 'inline-block',
                    maxWidth: 280,
                    whiteSpace: 'nowrap',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    verticalAlign: 'top',
                    color: 'text.primary',
                    px: 1.25,
                    py: 0.25,
                    borderRadius: 999,
                    border: 1,
                    borderColor: alpha(theme.palette.primary.main, 0.3),
                    transition:
                      'background-color 150ms ease-out, border-color 150ms ease-out',
                    '&:hover, &:focus-visible': {
                      bgcolor: alpha(theme.palette.primary.main, 0.12),
                      borderColor: 'primary.main',
                    },
                  })}
                >
                  {report.name}
                </Link>
              </Box>
            ))}
            {rest > 0 && (
              <Box
                component="li"
                sx={{
                  typography: 'body2',
                  color: 'text.secondary',
                  px: 0.5,
                  py: 0.25,
                }}
              >
                +{rest} more
              </Box>
            )}
          </Box>
        )}
      </Box>

      <Box sx={{ position: 'relative', zIndex: 1 }}>
        <RowMenu actions={actions} />
      </Box>
    </Box>
  );
}

function EmptyIndex({
  canWrite,
  onCreate,
}: {
  canWrite: boolean;
  onCreate: () => void;
}) {
  return (
    <Box
      sx={(theme) => ({
        display: 'flex',
        flexDirection: { xs: 'column', sm: 'row' },
        alignItems: { xs: 'flex-start', sm: 'center' },
        gap: { xs: 2, sm: 4 },
        px: { xs: 3, sm: 5 },
        py: { xs: 4, sm: 6 },
        borderRadius: 2,
        border: `1px dashed ${alpha(theme.palette.primary.main, 0.35)}`,
        bgcolor: alpha(theme.palette.primary.main, 0.04),
      })}
    >
      <SpaceConstellation seed="empty" groups={[]} size={96} />
      <Box>
        <Typography variant="h3" component="h2" sx={{ mb: 1 }}>
          No spaces yet
        </Typography>
        <Typography color="text.secondary" sx={{ maxWidth: '60ch', mb: 2.5 }}>
          A space collects related reports under one name, such as a team, an
          environment or an investigation, with sub-spaces to group them and an
          overview it opens on.
        </Typography>
        {canWrite && (
          <Button
            variant="contained"
            startIcon={<AddIcon />}
            onClick={onCreate}
          >
            Create the first space
          </Button>
        )}
      </Box>
    </Box>
  );
}

export default Spaces;
