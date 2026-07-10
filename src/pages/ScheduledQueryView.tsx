import { useEffect, useState } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import { Helmet } from 'react-helmet';
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  Grid,
  Typography,
} from '@mui/material';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import DeleteIcon from '@mui/icons-material/Delete';
import EditIcon from '@mui/icons-material/Edit';
import FiberManualRecordIcon from '@mui/icons-material/FiberManualRecord';
import HistoryIcon from '@mui/icons-material/History';
import ConfirmDeleteDialog from 'src/components/ConfirmDeleteDialog';
import ListPageHeader from 'src/components/ListPageHeader';
import ListViewState from 'src/components/ListViewState';
import RowMenu, { RowMenuAction } from 'src/components/RowMenu';
import ScheduledQueryDialog from 'src/components/ScheduledQueryDialog';
import UserDisplay from 'src/components/UserDisplay';
import { ActionConfigFieldDef, SeizuConfig } from 'src/config.context';
import {
  ScheduledQueryItem,
  useScheduledQuery,
  useScheduledQueriesMutations,
} from 'src/hooks/useScheduledQueriesApi';
import { usePermissions } from 'src/hooks/usePermissions';
import type { BackState } from 'src/navigation';
import { describeSchedule } from 'src/scheduleSpec';
import { pageContentSx } from 'src/theme/layout';

function triggerLabel(item: ScheduledQueryItem): string {
  if (item.watch_scans.length > 0)
    return `Watch scans (${item.watch_scans.length})`;
  if (item.schedule) return describeSchedule(item.schedule);
  if (item.frequency != null) return `Every ${item.frequency} min`;
  return 'Not configured';
}

function DetailItem({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <Box sx={{ mb: 1.5 }}>
      <Typography
        variant="caption"
        sx={{ color: 'text.secondary', display: 'block' }}
      >
        {label}
      </Typography>
      <Typography component="div" variant="body2">
        {children}
      </Typography>
    </Box>
  );
}

function QueryPanels({ query }: { query: ScheduledQueryItem }) {
  return (
    <>
      <Card variant="outlined" sx={{ mb: 2 }}>
        <CardContent>
          <Typography variant="subtitle2" sx={{ mb: 1.5 }}>
            Details
          </Typography>
          <DetailItem label="Status">
            <Chip
              label={query.enabled ? 'Enabled' : 'Disabled'}
              color={query.enabled ? 'success' : 'default'}
              size="small"
            />
          </DetailItem>
          <DetailItem label="Trigger">{triggerLabel(query)}</DetailItem>
          {query.watch_scans.length > 0 && (
            <DetailItem label="Watch scans">
              {query.watch_scans.map((ws, i) => (
                <Box key={i} component="span" sx={{ display: 'block' }}>
                  {[ws.grouptype, ws.syncedtype, ws.groupid]
                    .map((v) => v || '.*')
                    .join(' / ')}
                </Box>
              ))}
            </DetailItem>
          )}
          <DetailItem label="Version">v{query.current_version}</DetailItem>
          <DetailItem label="Owner">
            <UserDisplay userId={query.created_by} />
          </DetailItem>
          <DetailItem label="Last run">
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
              <FiberManualRecordIcon
                sx={{
                  fontSize: 12,
                  color:
                    query.last_run_status === 'success'
                      ? 'success.main'
                      : query.last_run_status === 'failure'
                        ? 'error.main'
                        : 'warning.main',
                }}
              />
              <Typography variant="body2" component="span">
                {query.last_run_status === 'success'
                  ? 'Success'
                  : query.last_run_status === 'failure'
                    ? 'Failed'
                    : 'No runs yet'}
              </Typography>
              {query.last_run_at && (
                <Typography
                  variant="body2"
                  color="text.secondary"
                  component="span"
                >
                  {new Date(query.last_run_at).toLocaleString()}
                </Typography>
              )}
            </Box>
          </DetailItem>
          <DetailItem label="Updated">
            {new Date(query.updated_at).toLocaleString()}
          </DetailItem>
        </CardContent>
      </Card>
      {query.last_errors.length > 0 && (
        <Card variant="outlined" sx={{ mb: 2 }}>
          <CardContent>
            <Typography variant="subtitle2" sx={{ mb: 1.5 }}>
              Recent errors
            </Typography>
            {query.last_errors.map((entry, i) => (
              <Alert key={i} severity="error" sx={{ mb: 1 }}>
                <Typography variant="caption" sx={{ display: 'block' }}>
                  {new Date(entry.timestamp).toLocaleString()}
                </Typography>
                {entry.error}
              </Alert>
            ))}
          </CardContent>
        </Card>
      )}
    </>
  );
}

function ScheduledQueryView() {
  const { id } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const hasPermission = usePermissions();
  const { fromLabel } = (location.state ?? {}) as BackState;

  const { query, loading, error, refresh } = useScheduledQuery(id ?? null);
  const { updateScheduledQuery, deleteScheduledQuery } =
    useScheduledQueriesMutations();

  const [editOpen, setEditOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [actionConfig, setActionConfig] = useState<SeizuConfig>({
    scheduled_query_action_types: [],
    scheduled_query_action_schemas: {} as Record<
      string,
      ActionConfigFieldDef[]
    >,
  });

  useEffect(() => {
    let cancelled = false;
    fetch('/api/v1/config')
      .then((res) => res.json())
      .then((config: SeizuConfig) => {
        if (!cancelled) {
          setActionConfig({
            scheduled_query_action_types:
              config.scheduled_query_action_types ?? [],
            scheduled_query_action_schemas:
              config.scheduled_query_action_schemas ?? {},
          });
        }
      })
      .catch(() => {
        /* action config is optional; dialog will allow freetext entry */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const canWrite = hasPermission('scheduled_queries:write');
  const canDelete = hasPermission('scheduled_queries:delete');

  const handleConfirmDelete = async () => {
    if (!id) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      await deleteScheduledQuery(id);
      navigate('/app/scheduled-queries');
    } catch {
      setDeleteError('Failed to delete scheduled query. Please try again.');
      setDeleting(false);
    }
  };

  const menuActions: RowMenuAction[] = [
    {
      key: 'history',
      label: 'View history',
      icon: <HistoryIcon fontSize="small" />,
      onClick: () =>
        navigate(`/app/scheduled-queries/${id}/history`, {
          state: {
            fromLabel: query?.name ?? 'Scheduled Query',
          } satisfies BackState,
        }),
    },
    {
      key: 'delete',
      label: 'Delete',
      icon: <DeleteIcon fontSize="small" />,
      onClick: () => {
        setDeleteError(null);
        setDeleteOpen(true);
      },
      disabled: !canDelete,
      tooltip: canDelete
        ? undefined
        : 'You do not have permission to delete scheduled queries',
      destructive: true,
      dividerBefore: true,
    },
  ];

  return (
    <>
      <Helmet>
        <title>
          {query ? `${query.name} | Seizu` : 'Scheduled Query | Seizu'}
        </title>
      </Helmet>
      <Box sx={pageContentSx}>
        {fromLabel && (
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
            <Button
              size="small"
              startIcon={<ArrowBackIcon />}
              onClick={() => navigate(-1)}
            >
              Back to {fromLabel}
            </Button>
          </Box>
        )}
        <ListPageHeader
          title={query?.name ?? 'Scheduled Query'}
          action={
            <Box sx={{ alignItems: 'center', display: 'flex', gap: 1 }}>
              <Button
                variant="contained"
                startIcon={<EditIcon />}
                onClick={() => setEditOpen(true)}
                disabled={!canWrite}
                title={
                  canWrite
                    ? undefined
                    : 'You do not have permission to edit scheduled queries'
                }
              >
                Edit
              </Button>
              <RowMenu actions={menuActions} />
            </Box>
          }
        />
        <ListViewState
          loading={loading}
          error={error}
          errorMessage="Failed to load scheduled query"
        >
          {query ? (
            <Grid container spacing={2}>
              <Grid size={{ xs: 12, md: 4 }}>
                <QueryPanels query={query} />
              </Grid>
              <Grid size={{ xs: 12, md: 8 }}>
                <Card variant="outlined" sx={{ mb: 2 }}>
                  <CardContent>
                    <Typography variant="subtitle2" sx={{ mb: 1.5 }}>
                      Cypher
                    </Typography>
                    <Box
                      component="pre"
                      sx={{
                        bgcolor: 'action.hover',
                        borderRadius: 1,
                        fontFamily: 'monospace',
                        fontSize: 13,
                        m: 0,
                        overflow: 'auto',
                        p: 1.5,
                        whiteSpace: 'pre-wrap',
                        wordBreak: 'break-word',
                      }}
                    >
                      {query.cypher}
                    </Box>
                  </CardContent>
                </Card>
                {query.params.length > 0 && (
                  <Card variant="outlined" sx={{ mb: 2 }}>
                    <CardContent>
                      <Typography variant="subtitle2" sx={{ mb: 1.5 }}>
                        Parameters
                      </Typography>
                      <Box
                        sx={{
                          display: 'flex',
                          flexDirection: 'column',
                          gap: 0.5,
                        }}
                      >
                        {query.params.map((p, i) => (
                          <Box key={i} sx={{ display: 'flex', gap: 1 }}>
                            <Typography
                              variant="body2"
                              sx={{
                                fontFamily: 'monospace',
                                fontWeight: 600,
                                minWidth: 120,
                              }}
                            >
                              {p.name}
                            </Typography>
                            <Typography
                              variant="body2"
                              color="text.secondary"
                              sx={{ fontFamily: 'monospace' }}
                            >
                              {Array.isArray(p.value)
                                ? (p.value as unknown[]).join(', ')
                                : String(p.value ?? '')}
                              {Array.isArray(p.value) && (
                                <Typography
                                  component="span"
                                  variant="caption"
                                  color="text.disabled"
                                  sx={{ ml: 0.5 }}
                                >
                                  (list)
                                </Typography>
                              )}
                            </Typography>
                          </Box>
                        ))}
                      </Box>
                    </CardContent>
                  </Card>
                )}
                {query.actions.length > 0 && (
                  <Card variant="outlined">
                    <CardContent>
                      <Typography variant="subtitle2" sx={{ mb: 1.5 }}>
                        Actions
                      </Typography>
                      <Box
                        sx={{
                          display: 'flex',
                          flexDirection: 'column',
                          gap: 1,
                        }}
                      >
                        {query.actions.map((a, i) => (
                          <Box
                            key={i}
                            sx={{
                              border: '1px solid',
                              borderColor: 'divider',
                              borderRadius: 1,
                              p: 1.5,
                            }}
                          >
                            <Typography
                              variant="body2"
                              sx={{ fontWeight: 600, mb: 0.5 }}
                            >
                              {a.action_type}
                            </Typography>
                            {Object.keys(a.action_config).length > 0 && (
                              <Box
                                sx={{
                                  display: 'flex',
                                  flexDirection: 'column',
                                  gap: 0.25,
                                }}
                              >
                                {Object.entries(a.action_config).map(
                                  ([k, v]) => (
                                    <Box
                                      key={k}
                                      sx={{ display: 'flex', gap: 1 }}
                                    >
                                      <Typography
                                        variant="caption"
                                        color="text.secondary"
                                        sx={{
                                          fontFamily: 'monospace',
                                          minWidth: 140,
                                        }}
                                      >
                                        {k}
                                      </Typography>
                                      <Typography
                                        variant="caption"
                                        color="text.secondary"
                                        sx={{ fontFamily: 'monospace' }}
                                      >
                                        {Array.isArray(v)
                                          ? (v as unknown[]).join(', ')
                                          : String(v ?? '')}
                                      </Typography>
                                    </Box>
                                  ),
                                )}
                              </Box>
                            )}
                          </Box>
                        ))}
                      </Box>
                    </CardContent>
                  </Card>
                )}
              </Grid>
            </Grid>
          ) : (
            <Alert severity="warning">Scheduled query not found.</Alert>
          )}
        </ListViewState>
      </Box>

      {editOpen && query ? (
        <ScheduledQueryDialog
          key={query.scheduled_query_id}
          open={editOpen}
          initial={query}
          onClose={() => setEditOpen(false)}
          onSave={async (req) => {
            await updateScheduledQuery(query.scheduled_query_id, req);
            await refresh();
          }}
          actionTypes={actionConfig.scheduled_query_action_types}
          actionSchemas={actionConfig.scheduled_query_action_schemas}
        />
      ) : null}

      <ConfirmDeleteDialog
        open={deleteOpen}
        title="Delete scheduled query?"
        onClose={() => setDeleteOpen(false)}
        onConfirm={() => void handleConfirmDelete()}
        deleting={deleting}
        error={deleteError}
      >
        Permanently delete <strong>{query?.name}</strong> and all its versions?
        This cannot be undone.
      </ConfirmDeleteDialog>
    </>
  );
}

export default ScheduledQueryView;
