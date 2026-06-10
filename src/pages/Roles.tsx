import { useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Alert,
  Box,
  Button,
  Checkbox,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  FormControlLabel,
  Paper,
  TextField,
  Typography,
} from '@mui/material';
import ConstellationSpinner from 'src/components/ConstellationSpinner';
import AddIcon from '@mui/icons-material/Add';
import BadgeIcon from '@mui/icons-material/Badge';
import DeleteIcon from '@mui/icons-material/Delete';
import EditIcon from '@mui/icons-material/Edit';
import HistoryIcon from '@mui/icons-material/History';
import PersonOutlineIcon from '@mui/icons-material/Person';
import VisibilityIcon from '@mui/icons-material/Visibility';
import {
  CreateRoleRequest,
  RoleItem,
  UpdateRoleRequest,
  isBuiltinRole,
  useBuiltinRolesList,
  useRoleMutations,
  useRolesList,
} from 'src/hooks/useRolesApi';
import UserDisplay from 'src/components/UserDisplay';
import { usePermissionState } from 'src/hooks/usePermissions';
import ListTable, {
  ListTableColumn,
  ListTableFilterGroup,
  listTableActionColumnSx,
  listTablePrimaryCellSx,
  listTableSecondaryCellSx,
  listTableTruncateSx,
} from 'src/components/ListTable';
import ListPageHeader from 'src/components/ListPageHeader';
import ListViewState from 'src/components/ListViewState';
import RowMenu, { RowMenuAction } from 'src/components/RowMenu';
import ConfirmDeleteDialog from 'src/components/ConfirmDeleteDialog';
import type { BackState } from 'src/navigation';
import { pageContentSx } from 'src/theme/layout';

const descriptionColumnSx = { ...listTableSecondaryCellSx, width: '22%' };
const permissionsColumnSx = { width: '18%' };
const roleTypeColumnSx = { width: 144 };
const versionColumnSx = { ...listTableSecondaryCellSx, width: 96 };
const updatedAtColumnSx = { ...listTableSecondaryCellSx, width: 180 };
const updatedByColumnSx = { ...listTableSecondaryCellSx, width: 150 };
const srOnlySx = {
  position: 'absolute',
  width: 1,
  height: 1,
  p: 0,
  m: -1,
  overflow: 'hidden',
  clip: 'rect(0, 0, 0, 0)',
  whiteSpace: 'nowrap',
  border: 0,
} as const;

function permissionGroup(permission: string): string {
  return permission.split(':')[0] || 'other';
}

function groupedPermissions(permissions: string[]): Record<string, string[]> {
  return permissions
    .slice()
    .sort()
    .reduce<Record<string, string[]>>((acc, permission) => {
      const group = permissionGroup(permission);
      acc[group] = [...(acc[group] ?? []), permission];
      return acc;
    }, {});
}

function permissionLabel(group: string): string {
  return group.replace(/_/g, ' ');
}

interface PermissionChipsSummaryProps {
  permissions: string[];
}

function PermissionChipsSummary({ permissions }: PermissionChipsSummaryProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const permissionRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const overflowRefs = useRef<Record<number, HTMLDivElement | null>>({});
  const [availableWidth, setAvailableWidth] = useState(0);
  const [visibleCount, setVisibleCount] = useState(permissions.length);
  const gapPx = 4;

  useLayoutEffect(() => {
    setVisibleCount(permissions.length);
  }, [permissions]);

  useLayoutEffect(() => {
    const updateWidth = () => {
      const width = containerRef.current?.getBoundingClientRect().width ?? 0;
      setAvailableWidth(width);
    };

    updateWidth();

    if (!containerRef.current || typeof ResizeObserver === 'undefined')
      return undefined;

    const observer = new ResizeObserver(updateWidth);
    observer.observe(containerRef.current);
    return () => observer.disconnect();
  }, []);

  useLayoutEffect(() => {
    if (availableWidth <= 0) return;

    const permissionWidths = permissions.map(
      (permission) =>
        permissionRefs.current[permission]?.getBoundingClientRect().width ?? 0,
    );
    const overflowWidths = Array.from(
      { length: permissions.length + 1 },
      (_, index) =>
        overflowRefs.current[index]?.getBoundingClientRect().width ?? 0,
    );

    let nextVisibleCount = 0;
    for (let count = permissions.length; count >= 0; count -= 1) {
      const remaining = permissions.length - count;
      const chipCount = count + (remaining > 0 ? 1 : 0);
      const totalWidth =
        permissionWidths
          .slice(0, count)
          .reduce((sum, width) => sum + width, 0) +
        (chipCount > 0 ? gapPx * (chipCount - 1) : 0) +
        (remaining > 0 ? (overflowWidths[remaining] ?? 0) : 0);
      if (totalWidth <= availableWidth) {
        nextVisibleCount = count;
        break;
      }
    }

    if (nextVisibleCount !== visibleCount) {
      setVisibleCount(nextVisibleCount);
    }
  }, [availableWidth, permissions, visibleCount]);

  const visiblePermissions = permissions.slice(0, visibleCount);
  const remaining = permissions.length - visibleCount;

  return (
    <Box
      ref={containerRef}
      sx={{ position: 'relative', minWidth: 0, overflow: 'hidden' }}
    >
      <Box
        sx={{
          display: 'flex',
          flexWrap: 'nowrap',
          gap: 0.5,
          overflow: 'hidden',
          minWidth: 0,
        }}
      >
        {visiblePermissions.map((permission) => (
          <Chip
            key={permission}
            label={permission}
            size="small"
            variant="outlined"
          />
        ))}
        {remaining > 0 && (
          <Chip label={`+${remaining}`} size="small" variant="outlined" />
        )}
      </Box>
      <Box
        aria-hidden
        sx={{
          position: 'absolute',
          inset: 0,
          visibility: 'hidden',
          pointerEvents: 'none',
          overflow: 'hidden',
          display: 'flex',
          flexWrap: 'nowrap',
          gap: 0.5,
          minWidth: 0,
        }}
      >
        {permissions.map((permission) => (
          <Chip
            key={`measure-${permission}`}
            label={permission}
            size="small"
            variant="outlined"
            ref={(node) => {
              permissionRefs.current[permission] =
                node as HTMLDivElement | null;
            }}
          />
        ))}
        {Array.from({ length: permissions.length + 1 }, (_, index) => (
          <Chip
            key={`overflow-${index}`}
            label={`+${index}`}
            size="small"
            variant="outlined"
            ref={(node) => {
              overflowRefs.current[index] = node as HTMLDivElement | null;
            }}
          />
        ))}
      </Box>
    </Box>
  );
}

interface RoleDetailDialogProps {
  role: RoleItem | null;
  onClose: () => void;
}

function RoleDetailDialog({ role, onClose }: RoleDetailDialogProps) {
  const groups = useMemo(
    () => groupedPermissions(role?.permissions ?? []),
    [role],
  );

  return (
    <Dialog open={!!role} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>{role?.name ?? 'Role details'}</DialogTitle>
      <DialogContent dividers>
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          <Box>
            <Typography variant="subtitle2" color="text.secondary">
              Description
            </Typography>
            <Typography>{role?.description || '-'}</Typography>
          </Box>
          <Box>
            <Typography
              variant="subtitle2"
              color="text.secondary"
              sx={{ mb: 1 }}
            >
              Permissions
            </Typography>
            {Object.keys(groups).length === 0 ? (
              <Typography color="text.secondary">No permissions.</Typography>
            ) : (
              <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                {Object.entries(groups).map(([group, permissions]) => (
                  <Box key={group}>
                    <Typography
                      variant="body2"
                      sx={{
                        fontWeight: 700,
                        textTransform: 'capitalize',
                        mb: 0.75,
                      }}
                    >
                      {permissionLabel(group)}
                    </Typography>
                    <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.75 }}>
                      {permissions.map((permission) => (
                        <Chip
                          key={permission}
                          label={permission}
                          size="small"
                          variant="outlined"
                        />
                      ))}
                    </Box>
                  </Box>
                ))}
              </Box>
            )}
          </Box>
        </Box>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Close</Button>
      </DialogActions>
    </Dialog>
  );
}

interface RoleDialogProps {
  open: boolean;
  onClose: () => void;
  onSave: (req: CreateRoleRequest | UpdateRoleRequest) => Promise<void>;
  initial: RoleItem | null;
  availablePermissions: string[];
}

function RoleDialog({
  open,
  onClose,
  onSave,
  initial,
  availablePermissions,
}: RoleDialogProps) {
  const [name, setName] = useState(initial?.name ?? '');
  const [description, setDescription] = useState(initial?.description ?? '');
  const [permissions, setPermissions] = useState<string[]>(
    initial?.permissions ?? [],
  );
  const [comment, setComment] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const groups = useMemo(
    () => groupedPermissions(availablePermissions),
    [availablePermissions],
  );

  const handleClose = () => {
    setError(null);
    onClose();
  };

  const togglePermission = (permission: string) => {
    setPermissions((current) =>
      current.includes(permission)
        ? current.filter((p) => p !== permission)
        : [...current, permission].sort(),
    );
  };

  const handleSave = async () => {
    setError(null);
    if (!name.trim()) {
      setError('Name is required.');
      return;
    }
    if (permissions.length === 0) {
      setError('Select at least one permission.');
      return;
    }

    const req = initial
      ? ({
          name: name.trim(),
          description: description.trim(),
          permissions,
          comment: comment.trim() || null,
        } as UpdateRoleRequest)
      : ({
          name: name.trim(),
          description: description.trim(),
          permissions,
        } as CreateRoleRequest);

    setSaving(true);
    try {
      await onSave(req);
      handleClose();
    } catch (err) {
      setError((err as Error)?.message ?? 'Failed to save role.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onClose={handleClose} maxWidth="md" fullWidth>
      <DialogTitle>{initial ? 'Edit Role' : 'New Role'}</DialogTitle>
      <DialogContent dividers>
        {error && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {error}
          </Alert>
        )}
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          <TextField
            label="Name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            fullWidth
            required
          />
          <TextField
            label="Description"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            fullWidth
            multiline
            minRows={2}
          />
          <Box>
            <Typography variant="subtitle2" sx={{ mb: 1 }}>
              Permissions
            </Typography>
            {Object.keys(groups).length === 0 ? (
              <Typography color="text.secondary">
                No permissions are available from built-in role definitions.
              </Typography>
            ) : (
              <Box
                sx={{
                  display: 'grid',
                  gridTemplateColumns: {
                    xs: '1fr',
                    md: 'repeat(2, minmax(0, 1fr))',
                  },
                  gap: 2,
                }}
              >
                {Object.entries(groups).map(([group, groupPermissions]) => (
                  <Paper key={group} variant="outlined" sx={{ p: 1.5 }}>
                    <Typography
                      variant="body2"
                      sx={{
                        fontWeight: 700,
                        textTransform: 'capitalize',
                        mb: 0.5,
                      }}
                    >
                      {permissionLabel(group)}
                    </Typography>
                    <Box sx={{ display: 'flex', flexDirection: 'column' }}>
                      {groupPermissions.map((permission) => (
                        <FormControlLabel
                          key={permission}
                          control={
                            <Checkbox
                              checked={permissions.includes(permission)}
                              onChange={() => togglePermission(permission)}
                            />
                          }
                          label={permission}
                        />
                      ))}
                    </Box>
                  </Paper>
                ))}
              </Box>
            )}
          </Box>
          {initial && (
            <TextField
              label="Comment (optional)"
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              fullWidth
              size="small"
              placeholder="Describe what changed..."
            />
          )}
        </Box>
      </DialogContent>
      <DialogActions>
        <Button onClick={handleClose} disabled={saving}>
          Cancel
        </Button>
        <Button onClick={handleSave} variant="contained" disabled={saving}>
          {saving ? <ConstellationSpinner size={20} /> : 'Save'}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

function Roles() {
  const navigate = useNavigate();
  const { hasPermission, loading: permissionsLoading } = usePermissionState();
  const canRead = hasPermission('roles:read');
  const {
    roles: builtinRoles,
    loading: builtinLoading,
    error: builtinError,
  } = useBuiltinRolesList(canRead);
  const { roles, loading, error, refresh } = useRolesList(canRead);
  const { createRole, updateRole, deleteRole } = useRoleMutations();

  const [dialogOpen, setDialogOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<RoleItem | null>(null);
  const [detailTarget, setDetailTarget] = useState<RoleItem | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<RoleItem | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [missingPermsTarget, setMissingPermsTarget] = useState<RoleItem | null>(
    null,
  );
  const [missingPermNames, setMissingPermNames] = useState<string[]>([]);

  const allRows = useMemo(
    () => [...builtinRoles, ...roles],
    [builtinRoles, roles],
  );
  const availablePermissions = useMemo(
    () =>
      Array.from(
        new Set(builtinRoles.flatMap((role) => role.permissions)),
      ).sort(),
    [builtinRoles],
  );

  const handleSave = async (req: CreateRoleRequest | UpdateRoleRequest) => {
    if (editTarget) {
      await updateRole(editTarget.role_id, req as UpdateRoleRequest);
    } else {
      await createRole(req as CreateRoleRequest);
    }
    refresh();
  };

  const handleEditClick = (role: RoleItem) => {
    const knownSet = new Set(builtinRoles.flatMap((r) => r.permissions));
    const missing = role.permissions.filter((p) => !knownSet.has(p));
    if (missing.length > 0) {
      setEditTarget(null);
      setMissingPermsTarget(role);
      setMissingPermNames(missing);
    } else {
      setEditTarget(role);
      setDialogOpen(true);
    }
  };

  const handleMissingPermsConfirm = () => {
    if (!missingPermsTarget) return;
    const knownSet = new Set(builtinRoles.flatMap((r) => r.permissions));
    const filtered = {
      ...missingPermsTarget,
      permissions: missingPermsTarget.permissions.filter((p) =>
        knownSet.has(p),
      ),
    };
    setEditTarget(filtered);
    setDialogOpen(true);
    setMissingPermsTarget(null);
    setMissingPermNames([]);
  };

  const handleDeleteConfirm = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await deleteRole(deleteTarget.role_id);
      setDeleteTarget(null);
      refresh();
    } finally {
      setDeleting(false);
    }
  };

  if (permissionsLoading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', p: 4 }}>
        <ConstellationSpinner size={48} />
      </Box>
    );
  }

  if (!canRead) {
    return (
      <Box sx={pageContentSx}>
        <Typography>You do not have access to role management.</Typography>
      </Box>
    );
  }

  const isLoading = builtinLoading || loading;
  const loadError = builtinError || error;
  const rowActions = (item: RoleItem): RowMenuAction[] => {
    const isBuiltin = isBuiltinRole(item.role_id);
    const canWrite = hasPermission('roles:write');
    const canDelete = hasPermission('roles:delete');
    return [
      {
        key: 'view',
        label: 'View detail',
        icon: <VisibilityIcon fontSize="small" />,
        onClick: () => setDetailTarget(item),
      },
      {
        key: 'edit',
        label: 'Edit',
        icon: <EditIcon fontSize="small" />,
        onClick: () => handleEditClick(item),
        disabled: isBuiltin || !canWrite,
        tooltip: isBuiltin
          ? 'Built-in roles cannot be edited'
          : !canWrite
            ? 'You do not have permission to edit roles'
            : undefined,
      },
      {
        key: 'history',
        label: 'View history',
        icon: <HistoryIcon fontSize="small" />,
        onClick: () =>
          navigate(`/app/roles/${item.role_id}/history`, {
            state: { fromLabel: 'Roles' } satisfies BackState,
          }),
        disabled: isBuiltin,
        tooltip: isBuiltin
          ? 'Built-in roles have no version history'
          : undefined,
      },
      {
        key: 'delete',
        label: 'Delete',
        icon: <DeleteIcon fontSize="small" />,
        onClick: () => setDeleteTarget(item),
        disabled: isBuiltin || !canDelete,
        tooltip: isBuiltin
          ? 'Built-in roles cannot be deleted'
          : !canDelete
            ? 'You do not have permission to delete roles'
            : undefined,
        destructive: true,
        dividerBefore: true,
      },
    ];
  };

  const columns: ListTableColumn<RoleItem>[] = [
    {
      key: 'name',
      label: 'Name',
      cellSx: { ...listTablePrimaryCellSx, width: '24%' },
      render: (item) => (
        <Button
          variant="text"
          size="small"
          onClick={() => setDetailTarget(item)}
          sx={[
            {
              px: 0,
              py: 0,
              minWidth: 0,
              maxWidth: '100%',
              justifyContent: 'flex-start',
              color: 'text.primary',
              fontSize: '0.875rem',
              lineHeight: 1.43,
              textTransform: 'none',
              fontWeight: 500,
              '&:hover': {
                backgroundColor: 'transparent',
                textDecoration: 'underline',
              },
            },
            listTableTruncateSx,
          ]}
        >
          {item.name}
        </Button>
      ),
    },
    {
      key: 'type',
      label: 'Type',
      cellSx: roleTypeColumnSx,
      render: (item) => {
        const builtin = isBuiltinRole(item.role_id);
        return (
          <Chip
            label={builtin ? 'Built-in' : 'User-defined'}
            size="small"
            variant={builtin ? 'outlined' : 'filled'}
            color={builtin ? 'primary' : 'default'}
          />
        );
      },
    },
    {
      key: 'description',
      label: 'Description',
      hideBelow: 'md',
      cellSx: descriptionColumnSx,
      render: (item) => (
        <Typography
          variant="body2"
          color="text.secondary"
          sx={listTableTruncateSx}
        >
          {item.description || '-'}
        </Typography>
      ),
    },
    {
      key: 'permissions',
      label: 'Permissions',
      hideBelow: 'lg',
      cellSx: permissionsColumnSx,
      render: (item) => {
        return (
          <Box sx={{ position: 'relative', minWidth: 0 }}>
            <Box sx={srOnlySx}>{item.permissions.join(' ')}</Box>
            <PermissionChipsSummary permissions={item.permissions} />
          </Box>
        );
      },
    },
    {
      key: 'version',
      label: 'Version',
      hideBelow: 'sm',
      cellSx: versionColumnSx,
      render: (item) =>
        isBuiltinRole(item.role_id) ? '-' : `v${item.current_version}`,
    },
    {
      key: 'updated_at',
      label: 'Last updated',
      hideBelow: 'xl',
      cellSx: updatedAtColumnSx,
      render: (item) =>
        isBuiltinRole(item.role_id) || !item.updated_at
          ? '-'
          : new Date(item.updated_at).toLocaleString(),
    },
    {
      key: 'updated_by',
      label: 'Updated by',
      hideBelow: 'lg',
      cellSx: updatedByColumnSx,
      render: (item) =>
        isBuiltinRole(item.role_id) ? (
          '-'
        ) : item.updated_by ? (
          <UserDisplay userId={item.updated_by} />
        ) : (
          <UserDisplay userId={item.created_by} />
        ),
    },
    {
      key: 'actions',
      align: 'right',
      cellSx: listTableActionColumnSx,
      render: (item) => (
        <RowMenu actions={rowActions(item)} menuMinWidth={190} />
      ),
    },
  ];
  const filterGroups: ListTableFilterGroup<RoleItem>[] = [
    {
      key: 'role_type',
      label: 'Role type',
      icon: <BadgeIcon fontSize="small" />,
      options: [
        {
          key: 'builtin',
          label: 'Built-in',
          icon: <BadgeIcon fontSize="small" />,
          matches: (item) => isBuiltinRole(item.role_id),
        },
        {
          key: 'user_defined',
          label: 'User-defined',
          icon: <PersonOutlineIcon fontSize="small" />,
          matches: (item) => !isBuiltinRole(item.role_id),
        },
      ],
    },
  ];

  return (
    <>
      <Box sx={pageContentSx}>
        <ListPageHeader
          title="Roles"
          action={
            hasPermission('roles:write') && (
              <Button
                variant="contained"
                startIcon={<AddIcon />}
                onClick={() => {
                  setEditTarget(null);
                  setDialogOpen(true);
                }}
              >
                New role
              </Button>
            )
          }
        />

        <ListViewState
          loading={isLoading}
          error={loadError}
          errorMessage="Failed to load roles"
        >
          <ListTable
            rows={allRows}
            columns={columns}
            getRowKey={(item) => item.role_id}
            emptyMessage="No roles found."
            filterGroups={filterGroups}
          />
        </ListViewState>
      </Box>

      <RoleDialog
        key={editTarget?.role_id ?? 'new'}
        open={dialogOpen}
        onClose={() => setDialogOpen(false)}
        onSave={handleSave}
        initial={editTarget}
        availablePermissions={availablePermissions}
      />

      <RoleDetailDialog
        role={detailTarget}
        onClose={() => setDetailTarget(null)}
      />

      <Dialog
        open={!!missingPermsTarget}
        onClose={() => {
          setMissingPermsTarget(null);
          setMissingPermNames([]);
        }}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>Remove unrecognized permissions?</DialogTitle>
        <DialogContent>
          <DialogContentText>
            The following permissions in this role are no longer recognized:
          </DialogContentText>
          <Box component="ul" sx={{ mt: 1, mb: 1, pl: 2 }}>
            {missingPermNames.map((p) => (
              <Box
                component="li"
                key={p}
                sx={{ fontFamily: 'monospace', fontSize: 13 }}
              >
                {p}
              </Box>
            ))}
          </Box>
          <DialogContentText>
            They will be removed when you save. Continue editing?
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button
            onClick={() => {
              setMissingPermsTarget(null);
              setMissingPermNames([]);
            }}
          >
            Cancel
          </Button>
          <Button variant="contained" onClick={handleMissingPermsConfirm}>
            Continue editing
          </Button>
        </DialogActions>
      </Dialog>

      <ConfirmDeleteDialog
        open={!!deleteTarget}
        title="Delete role?"
        deleting={deleting}
        onClose={() => setDeleteTarget(null)}
        onConfirm={handleDeleteConfirm}
      >
        Permanently delete <strong>{deleteTarget?.name}</strong> and all its
        versions? This cannot be undone.
      </ConfirmDeleteDialog>
    </>
  );
}

export default Roles;
