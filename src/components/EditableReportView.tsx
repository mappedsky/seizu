import {
  useState,
  useRef,
  useEffect,
  memo,
  useCallback,
  type Ref,
} from 'react';
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Alert,
  Box,
  Button,
  Checkbox,
  Chip,
  Container,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  FormControl,
  FormControlLabel,
  IconButton,
  InputLabel,
  Menu,
  MenuItem,
  Paper,
  Select,
  Slider,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material';
import ConstellationSpinner from 'src/components/ConstellationSpinner';
import Add from '@mui/icons-material/Add';
import CancelIcon from '@mui/icons-material/Cancel';
import DeleteIcon from '@mui/icons-material/Delete';
import DragIndicatorIcon from '@mui/icons-material/DragIndicator';
import EditIcon from '@mui/icons-material/Edit';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import OpenWithIcon from '@mui/icons-material/OpenWith';
import OpenInFullIcon from '@mui/icons-material/OpenInFull';
import RemoveIcon from '@mui/icons-material/Remove';
import SaveIcon from '@mui/icons-material/Save';
import type { ResponsiveLayouts, Layout as RglLayout } from 'react-grid-layout';
import {
  DndContext,
  PointerSensor,
  KeyboardSensor,
  useSensor,
  useSensors,
  closestCenter,
  type DragEndEvent,
} from '@dnd-kit/core';
import {
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
  arrayMove,
} from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';

import {
  Report,
  Row,
  Panel,
  ReportInput,
  InputValue,
} from 'src/config.context';
import PanelEditor, { EditablePanel } from 'src/components/reports/PanelEditor';
import { EditPanelSkeleton } from 'src/components/reports/PanelLoadingSkeletons';
import PanelGridRow from 'src/components/reports/PanelGridRow';
import type { ResponsiveBreakpoint } from 'src/components/reports/panelLayout';
import {
  DASHBOARD_NAVBAR_HEIGHT,
  DASHBOARD_SIDEBAR_WIDTH_VAR,
} from 'src/components/dashboardLayoutConstants';
import { contentContainerSx } from 'src/theme/layout';

// ---------------------------------------------------------------------------
// Edit-state types (panels/rows get stable _id for DnD keys)
// ---------------------------------------------------------------------------

export interface EditableRow {
  _id: string;
  name: string;
  hide_header?: boolean;
  collapsible?: boolean;
  panels: EditablePanel[];
}

let _counter = 0;
function uid() {
  _counter += 1;
  return `id-${Date.now()}-${_counter}`;
}

function toEditableRows(rows: Row[] | undefined): EditableRow[] {
  if (!Array.isArray(rows)) return [];
  return rows.map((row) => ({
    _id: uid(),
    ...row,
    panels: Array.isArray(row.panels)
      ? row.panels.map((p) => ({ ...p, _id: uid() }))
      : [],
  }));
}

function fromEditableRows(rows: EditableRow[]): Row[] {
  return rows.map(({ _id: _rid, ...row }) => ({
    ...row,
    panels: row.panels.map(({ _id: _pid, ...panel }) => panel as Panel),
  }));
}

// ---------------------------------------------------------------------------
// Sortable wrapper for row cards (applies dnd-kit transform to the outer div)
// ---------------------------------------------------------------------------

type DragHandleProps = Omit<React.ComponentPropsWithRef<'button'>, 'color'>;

interface SortableRowWrapperProps {
  id: string;
  children: (dragHandleProps: DragHandleProps) => React.ReactNode;
}

function SortableRowWrapper({ id, children }: SortableRowWrapperProps) {
  const {
    attributes,
    listeners,
    setNodeRef,
    setActivatorNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id });

  const wrapperStyle: React.CSSProperties = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.4 : 1,
  };

  return (
    <div ref={setNodeRef} style={wrapperStyle}>
      {children({
        ref: setActivatorNodeRef as React.Ref<HTMLButtonElement>,
        style: { touchAction: 'none', cursor: 'grab' },
        ...attributes,
        ...listeners,
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Panel card shown inside the editable view
// ---------------------------------------------------------------------------

function PanelTypeChip({ type }: { type: string }) {
  return (
    <Chip
      label={type}
      size="small"
      variant="outlined"
      color="primary"
      sx={{ fontSize: '0.65rem', height: 20 }}
    />
  );
}

interface EditablePanelCardProps {
  panel: EditablePanel;
  onEdit: () => void;
  onDelete: () => void;
  moveTargetRows: ReadonlyArray<{ id: string; name: string }>;
  onMoveToRow: (targetRowId: string) => void;
}

function EditablePanelCard({
  panel,
  onEdit,
  onDelete,
  moveTargetRows,
  onMoveToRow,
}: EditablePanelCardProps) {
  const [moveMenuAnchor, setMoveMenuAnchor] = useState<HTMLElement | null>(
    null,
  );
  const moveMenuOpen = Boolean(moveMenuAnchor);

  return (
    <Paper
      variant="outlined"
      sx={{
        position: 'relative',
        p: 1,
        height: '100%',
        minHeight: 0,
        display: 'flex',
        flexDirection: 'column',
        gap: 0.5,
        cursor: 'move',
        bgcolor: 'background.paper',
      }}
    >
      <Stack
        direction="row"
        spacing={0.5}
        sx={{ alignItems: 'center', flexWrap: 'wrap', flexShrink: 0 }}
      >
        <PanelTypeChip type={panel.type} />
        {panel.caption && (
          <Typography variant="caption" noWrap sx={{ maxWidth: 150 }}>
            {panel.caption}
          </Typography>
        )}
      </Stack>
      <Box sx={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
        <EditPanelSkeleton type={panel.type} />
      </Box>

      {/* Edit/delete controls. Drag and resize are handled by react-grid-layout. */}
      <Box
        sx={{
          display: 'flex',
          alignItems: 'center',
          gap: 0.25,
          flexShrink: 0,
          pr: 2.5,
        }}
      >
        <Box sx={{ flex: 1 }} />
        <Tooltip title="Edit panel">
          <IconButton aria-label="Edit panel" size="small" onClick={onEdit}>
            <EditIcon sx={{ fontSize: 14 }} />
          </IconButton>
        </Tooltip>
        <Tooltip title="Delete panel">
          <IconButton
            aria-label="Delete panel"
            size="small"
            color="error"
            onClick={onDelete}
          >
            <DeleteIcon sx={{ fontSize: 14 }} />
          </IconButton>
        </Tooltip>
        {moveTargetRows.length > 0 && (
          <>
            <Tooltip title="Move to row">
              <IconButton
                aria-label="Move to row"
                aria-controls={
                  moveMenuOpen ? `move-menu-${panel._id}` : undefined
                }
                aria-haspopup="true"
                aria-expanded={moveMenuOpen || undefined}
                size="small"
                onClick={(e) => setMoveMenuAnchor(e.currentTarget)}
              >
                <OpenWithIcon sx={{ fontSize: 14 }} />
              </IconButton>
            </Tooltip>
            <Menu
              id={`move-menu-${panel._id}`}
              anchorEl={moveMenuAnchor}
              open={moveMenuOpen}
              onClose={() => setMoveMenuAnchor(null)}
              anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
              transformOrigin={{ vertical: 'top', horizontal: 'right' }}
            >
              {moveTargetRows.map((row) => (
                <MenuItem
                  key={row.id}
                  dense
                  onClick={() => {
                    onMoveToRow(row.id);
                    setMoveMenuAnchor(null);
                  }}
                >
                  {row.name}
                </MenuItem>
              ))}
            </Menu>
          </>
        )}
      </Box>
      <Box
        aria-hidden="true"
        sx={{
          position: 'absolute',
          right: 4,
          bottom: 4,
          width: 18,
          height: 18,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: 'text.secondary',
          opacity: 0.65,
          pointerEvents: 'none',
        }}
      >
        <OpenInFullIcon sx={{ fontSize: 15, transform: 'scaleX(-1)' }} />
      </Box>
    </Paper>
  );
}

// ---------------------------------------------------------------------------
// Inputs editor
// ---------------------------------------------------------------------------

const INPUT_TYPES = [
  { value: 'autocomplete', label: 'Autocomplete' },
  { value: 'text', label: 'Text' },
];

function emptyInput(): ReportInput {
  return { input_id: '', type: 'autocomplete', label: '', size: 3 };
}

interface InputCardProps {
  input: ReportInput;
  onEdit: () => void;
  onDelete: () => void;
  onResize: (delta: number) => void;
}

function InputCard({ input, onEdit, onDelete, onResize }: InputCardProps) {
  const capped = Math.max(1, Math.min(12, input.size ?? 3));
  return (
    <Paper
      variant="outlined"
      sx={{ p: 1.5, display: 'flex', alignItems: 'center', gap: 1 }}
    >
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Stack
          direction="row"
          spacing={0.5}
          sx={{ alignItems: 'center', flexWrap: 'wrap' }}
        >
          <Chip
            label={input.type}
            size="small"
            variant="outlined"
            color="secondary"
            sx={{ fontSize: '0.65rem', height: 20 }}
          />
          <Typography variant="body2" noWrap sx={{ fontWeight: 500 }}>
            {input.label || <em>no label</em>}
          </Typography>
          <Typography variant="caption" color="text.secondary" noWrap>
            id: {input.input_id || '—'}
          </Typography>
        </Stack>
      </Box>
      <Tooltip title="Decrease width">
        <span>
          <IconButton
            size="small"
            disabled={capped <= 1}
            onClick={() => onResize(-1)}
          >
            <RemoveIcon sx={{ fontSize: 14 }} />
          </IconButton>
        </span>
      </Tooltip>
      <Typography variant="caption" sx={{ minWidth: 16, textAlign: 'center' }}>
        {capped}
      </Typography>
      <Tooltip title="Increase width">
        <span>
          <IconButton
            size="small"
            disabled={capped >= 12}
            onClick={() => onResize(1)}
          >
            <Add sx={{ fontSize: 14 }} />
          </IconButton>
        </span>
      </Tooltip>
      <Tooltip title="Edit input">
        <IconButton size="small" onClick={onEdit}>
          <EditIcon sx={{ fontSize: 14 }} />
        </IconButton>
      </Tooltip>
      <Tooltip title="Delete input">
        <IconButton size="small" color="error" onClick={onDelete}>
          <DeleteIcon sx={{ fontSize: 14 }} />
        </IconButton>
      </Tooltip>
    </Paper>
  );
}

interface InputEditorDialogProps {
  open: boolean;
  input: ReportInput | null;
  onClose: () => void;
  onSave: (input: ReportInput) => void;
}

function InputEditorDialog({
  open,
  input,
  onClose,
  onSave,
}: InputEditorDialogProps) {
  const [form, setForm] = useState<ReportInput>(emptyInput());

  useEffect(() => {
    setForm(input ? { ...input } : emptyInput());
  }, [input, open]);

  function set<K extends keyof ReportInput>(key: K, value: ReportInput[K]) {
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  function setDefault(field: keyof InputValue, value: string) {
    setForm((prev) => ({
      ...prev,
      default: {
        label: prev.default?.label ?? '',
        value: prev.default?.value ?? '',
        [field]: value,
      },
    }));
  }

  const isAutocomplete = form.type === 'autocomplete';
  const canSave = form.input_id.trim() !== '' && form.label.trim() !== '';

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>{input ? 'Edit Input' : 'Add Input'}</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 1 }}>
          <FormControl fullWidth size="small">
            <InputLabel>Input type</InputLabel>
            <Select
              label="Input type"
              value={form.type}
              onChange={(e) => set('type', e.target.value)}
            >
              {INPUT_TYPES.map((t) => (
                <MenuItem key={t.value} value={t.value}>
                  {t.label}
                </MenuItem>
              ))}
            </Select>
          </FormControl>

          <TextField
            fullWidth
            size="small"
            label="Input ID"
            value={form.input_id}
            onChange={(e) => set('input_id', e.target.value)}
            helperText="Referenced from panel params via input_id."
          />

          <TextField
            fullWidth
            size="small"
            label="Label"
            value={form.label}
            onChange={(e) => set('label', e.target.value)}
            helperText="Shown to the user above the input."
          />

          <Box>
            <Typography gutterBottom variant="body2">
              Size (grid columns: {form.size ?? 3})
            </Typography>
            <Slider
              min={1}
              max={12}
              step={1}
              marks
              value={form.size ?? 3}
              onChange={(_, v) => set('size', v as number)}
              valueLabelDisplay="auto"
            />
          </Box>

          {isAutocomplete && (
            <>
              <Divider />
              <TextField
                fullWidth
                size="small"
                label="Cypher (options query)"
                multiline
                minRows={3}
                value={form.cypher ?? ''}
                onChange={(e) => set('cypher', e.target.value || undefined)}
                slotProps={{
                  htmlInput: {
                    style: { fontFamily: 'monospace', fontSize: '0.8rem' },
                  },
                }}
                helperText="Query to populate the dropdown. Must return a 'value' column."
              />
            </>
          )}

          <Divider />
          <Typography variant="body2" sx={{ fontWeight: 'medium' }}>
            Default value
          </Typography>
          <Stack direction="row" spacing={1}>
            <TextField
              size="small"
              label="Default label"
              value={form.default?.label ?? ''}
              onChange={(e) => setDefault('label', e.target.value)}
              sx={{ flex: 1 }}
              helperText="Display label for the default."
            />
            <TextField
              size="small"
              label="Default value"
              value={form.default?.value ?? ''}
              onChange={(e) => setDefault('value', e.target.value)}
              sx={{ flex: 1 }}
              helperText="The parameter value sent to queries."
            />
          </Stack>
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button
          variant="contained"
          onClick={() => onSave(cleanInput(form))}
          disabled={!canSave}
        >
          Save Input
        </Button>
      </DialogActions>
    </Dialog>
  );
}

function cleanInput(input: ReportInput): ReportInput {
  const result: ReportInput = {
    input_id: input.input_id.trim(),
    type: input.type,
    label: input.label.trim(),
  };
  if (input.size != null) result.size = Math.max(1, Math.min(12, input.size));
  if (input.cypher) result.cypher = input.cypher;
  if (input.default?.value)
    result.default = { label: input.default.label, value: input.default.value };
  return result;
}

// ---------------------------------------------------------------------------
// Key-value editor for named queries
// ---------------------------------------------------------------------------

interface QueryRowProps {
  queryKey: string;
  value: string;
  onRename: (oldKey: string, newKey: string) => void;
  onValueChange: (key: string, value: string) => void;
  onDraftValueChange: (key: string, value: string) => void;
  onDelete: (key: string) => void;
}

// Isolated row component so that local key-name state doesn't cause the
// parent to re-key the entire list on every keystroke.
function QueryRow({
  queryKey,
  value,
  onRename,
  onValueChange,
  onDraftValueChange,
  onDelete,
}: QueryRowProps) {
  const [localKey, setLocalKey] = useState(queryKey);
  const [localValue, setLocalValue] = useState(value);

  // Keep local key in sync if the parent renames it externally
  if (
    localKey !== queryKey &&
    document.activeElement?.getAttribute('data-query-key') !== queryKey
  ) {
    setLocalKey(queryKey);
  }
  if (
    localValue !== value &&
    document.activeElement?.getAttribute('data-query-value') !== queryKey
  ) {
    setLocalValue(value);
  }

  return (
    <Box sx={{ display: 'flex', gap: 1, alignItems: 'flex-start' }}>
      <TextField
        size="small"
        label="Query name"
        value={localKey}
        onChange={(e) => setLocalKey(e.target.value)}
        onBlur={() => {
          const trimmed = localKey.trim();
          if (trimmed && trimmed !== queryKey) {
            onRename(queryKey, trimmed);
          } else {
            setLocalKey(queryKey); // revert if empty or unchanged
          }
        }}
        slotProps={{ htmlInput: { 'data-query-key': queryKey } }}
        sx={{ width: 220, flexShrink: 0 }}
        helperText="Used as cypher field in panels"
      />
      <TextField
        size="small"
        label="Cypher"
        multiline
        minRows={4}
        value={localValue}
        onChange={(e) => {
          setLocalValue(e.target.value);
          onDraftValueChange(queryKey, e.target.value);
        }}
        onBlur={() => {
          if (localValue !== value) onValueChange(queryKey, localValue);
        }}
        sx={{ flex: 1 }}
        slotProps={{
          htmlInput: {
            'data-query-value': queryKey,
            style: { fontFamily: 'monospace', fontSize: '0.8rem' },
          },
        }}
      />
      <Tooltip title="Delete query">
        <IconButton
          onClick={() => onDelete(queryKey)}
          size="small"
          sx={{ mt: 0.5 }}
        >
          <DeleteIcon fontSize="small" />
        </IconButton>
      </Tooltip>
    </Box>
  );
}

interface EditToolbarProps {
  initialReportName: string;
  saving: boolean;
  saveError: string | null;
  toolbarRef: Ref<HTMLDivElement>;
  onCancel: () => void;
  onSave: (reportName: string, saveComment: string) => void;
}

const EditToolbar = memo(function EditToolbar({
  initialReportName,
  saving,
  saveError,
  toolbarRef,
  onCancel,
  onSave,
}: EditToolbarProps) {
  const [reportName, setReportName] = useState(initialReportName);
  const [saveComment, setSaveComment] = useState('');

  useEffect(() => {
    setReportName(initialReportName);
  }, [initialReportName]);

  return (
    <Box
      ref={toolbarRef}
      sx={{
        position: 'fixed',
        top: DASHBOARD_NAVBAR_HEIGHT,
        left: { xs: 0, lg: `var(${DASHBOARD_SIDEBAR_WIDTH_VAR})` },
        right: 0,
        zIndex: (theme) => theme.zIndex.appBar - 1,
        bgcolor: 'background.default',
        borderBottom: '1px solid',
        borderColor: 'divider',
        boxShadow: 1,
        ...contentContainerSx,
        py: 2,
        display: 'flex',
        alignItems: 'center',
        gap: 1.5,
      }}
    >
      <Typography variant="body2" sx={{ fontWeight: 'medium', flexShrink: 0 }}>
        Editing report
      </Typography>
      <TextField
        size="small"
        label="Report name"
        value={reportName}
        onChange={(e) => setReportName(e.target.value)}
        sx={{ width: 260 }}
      />
      <TextField
        size="small"
        label="Save comment (optional)"
        value={saveComment}
        onChange={(e) => setSaveComment(e.target.value)}
        sx={{ flex: 1, maxWidth: 400 }}
      />
      {saveError && (
        <Typography variant="caption" color="error">
          {saveError}
        </Typography>
      )}
      <Box sx={{ ml: 'auto', display: 'flex', gap: 1 }}>
        <Button
          variant="outlined"
          size="small"
          startIcon={<CancelIcon />}
          onClick={onCancel}
          disabled={saving}
        >
          Cancel
        </Button>
        <Button
          variant="contained"
          size="small"
          startIcon={saving ? <ConstellationSpinner size={16} /> : <SaveIcon />}
          onClick={() => onSave(reportName, saveComment)}
          disabled={saving || !reportName.trim()}
        >
          Save version
        </Button>
      </Box>
    </Box>
  );
});

interface EditableRowCardProps {
  row: EditableRow;
  rowIndex: number;
  onRename: (rowId: string, name: string) => void;
  onUpdateRowProps: (
    rowId: string,
    updates: Partial<Pick<EditableRow, 'hide_header' | 'collapsible'>>,
  ) => void;
  onAddPanel: (rowId: string) => void;
  onDeleteRow: (rowId: string) => void;
  onEditPanel: (rowId: string, panelId: string) => void;
  onDeletePanel: (rowId: string, panelId: string) => void;
  onLayoutChange: (
    rowId: string,
    layouts: ResponsiveLayouts<ResponsiveBreakpoint>,
  ) => void;
  dragHandleProps: DragHandleProps;
  moveTargetRows: ReadonlyArray<{ id: string; name: string }>;
  onMovePanel: (panelId: string, targetRowId: string) => void;
}

const EditableRowCard = memo(function EditableRowCard({
  row,
  rowIndex,
  onRename,
  onUpdateRowProps,
  onAddPanel,
  onDeleteRow,
  onEditPanel,
  onDeletePanel,
  onLayoutChange,
  dragHandleProps,
  moveTargetRows,
  onMovePanel,
}: EditableRowCardProps) {
  const [rowName, setRowName] = useState(row.name);

  useEffect(() => {
    setRowName(row.name);
  }, [row._id, row.name]);

  const commitRowName = () => {
    const trimmed = rowName.trim();
    if (trimmed && trimmed !== row.name) {
      onRename(row._id, trimmed);
    } else if (!trimmed) {
      setRowName(row.name);
    }
  };

  return (
    <Container
      maxWidth={false}
      sx={{ ...contentContainerSx, pt: rowIndex === 0 ? 0.75 : 0, pb: 1.5 }}
    >
      <Paper elevation={1} sx={{ p: 1.5 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
          <Tooltip title="Drag to reorder row">
            <IconButton
              {...dragHandleProps}
              size="small"
              aria-label="Drag to reorder row"
              sx={{ cursor: 'grab', color: 'text.secondary', flexShrink: 0 }}
            >
              <DragIndicatorIcon fontSize="small" />
            </IconButton>
          </Tooltip>
          <TextField
            size="small"
            label="Row name"
            value={rowName}
            onChange={(e) => setRowName(e.target.value)}
            onBlur={commitRowName}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.currentTarget.blur();
              }
            }}
            sx={{ flex: 1, maxWidth: 360 }}
          />
          <Box sx={{ flex: 1 }} />
          <Tooltip title="Add panel to this row">
            <Button
              size="small"
              startIcon={<Add />}
              onClick={() => onAddPanel(row._id)}
            >
              Add panel
            </Button>
          </Tooltip>
          <Tooltip title="Delete row">
            <IconButton
              size="small"
              color="error"
              onClick={() => onDeleteRow(row._id)}
            >
              <DeleteIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        </Box>
        <Box sx={{ display: 'flex', gap: 2, ml: 0.5, mb: 1 }}>
          <FormControlLabel
            control={
              <Checkbox
                size="small"
                checked={row.hide_header === true}
                onChange={(e) =>
                  onUpdateRowProps(row._id, {
                    hide_header: e.target.checked || undefined,
                  })
                }
              />
            }
            label={<Typography variant="body2">Hide header</Typography>}
          />
          <FormControlLabel
            control={
              <Checkbox
                size="small"
                checked={row.collapsible !== false}
                onChange={(e) =>
                  onUpdateRowProps(row._id, {
                    collapsible: e.target.checked ? undefined : false,
                  })
                }
              />
            }
            label={<Typography variant="body2">Collapsible</Typography>}
          />
        </Box>
        <Divider sx={{ mb: 1.5 }} />

        {row.panels.length === 0 ? (
          <Box sx={{ minHeight: 80, display: 'flex', alignItems: 'center' }}>
            <Typography
              variant="body2"
              color="text.secondary"
              sx={{ py: 2, px: 1 }}
            >
              No panels yet. Click "Add panel" to add one.
            </Typography>
          </Box>
        ) : (
          <PanelGridRow
            panels={row.panels}
            isEditing
            onLayoutChange={(layouts) => onLayoutChange(row._id, layouts)}
            renderPanel={(_panel, idx) => {
              const panel = row.panels[idx];
              return (
                <EditablePanelCard
                  panel={panel}
                  onEdit={() => onEditPanel(row._id, panel._id)}
                  onDelete={() => onDeletePanel(row._id, panel._id)}
                  moveTargetRows={moveTargetRows}
                  onMoveToRow={(targetRowId) =>
                    onMovePanel(panel._id, targetRowId)
                  }
                />
              );
            }}
          />
        )}
      </Paper>
    </Container>
  );
});

interface NamedQueryEditorProps {
  queries: Record<string, string>;
  onChange: (q: Record<string, string>) => void;
  onDraftValueChange: (key: string, value: string) => void;
  onRename: (oldKey: string, newKey: string) => void;
}

function NamedQueryEditor({
  queries,
  onChange,
  onDraftValueChange,
  onRename,
}: NamedQueryEditorProps) {
  // Stable insertion-order list so React keys don't thrash when a key is renamed
  const [keyOrder, setKeyOrder] = useState<string[]>(() =>
    Object.keys(queries),
  );

  function renameKey(oldKey: string, newKey: string) {
    if (newKey === oldKey || newKey in queries) return;
    const next: Record<string, string> = {};
    for (const k of keyOrder) {
      next[k === oldKey ? newKey : k] = queries[k];
    }
    setKeyOrder((prev) => prev.map((k) => (k === oldKey ? newKey : k)));
    onChange(next);
    onRename(oldKey, newKey);
  }

  function updateValue(key: string, value: string) {
    onChange({ ...queries, [key]: value });
  }

  function addQuery() {
    const key = `query-${Date.now()}`;
    setKeyOrder((prev) => [...prev, key]);
    onChange({ ...queries, [key]: '' });
  }

  function removeQuery(key: string) {
    setKeyOrder((prev) => prev.filter((k) => k !== key));
    const next = { ...queries };
    delete next[key];
    onChange(next);
  }

  // Only show keys that still exist in the queries map (handles external resets)
  const orderedKeys = keyOrder.filter((k) => k in queries);

  return (
    <Stack spacing={2}>
      {orderedKeys.length === 0 && (
        <Typography variant="body2" color="text.secondary">
          No named queries yet. Add one below to reference it by key in panel
          Cypher fields.
        </Typography>
      )}
      {orderedKeys.map((key) => (
        <QueryRow
          key={key}
          queryKey={key}
          value={queries[key] ?? ''}
          onRename={renameKey}
          onValueChange={updateValue}
          onDraftValueChange={onDraftValueChange}
          onDelete={removeQuery}
        />
      ))}
      <Box>
        <Button size="small" startIcon={<Add />} onClick={addQuery}>
          Add named query
        </Button>
      </Box>
    </Stack>
  );
}

// ---------------------------------------------------------------------------
// Main EditableReportView
// ---------------------------------------------------------------------------

export interface EditableReportViewProps {
  report: Report;
  reportId: string;
  onSave: (report: Report, comment: string) => Promise<void>;
  onCancel: () => void;
}

function EditableReportView({
  report,
  reportId: _reportId,
  onSave,
  onCancel,
}: EditableReportViewProps) {
  const hasInvalidRows =
    !Array.isArray(report.rows) ||
    report.rows.some((row) => !Array.isArray(row.panels));
  const [namedQueries, setNamedQueries] = useState<Record<string, string>>(
    report.queries ?? {},
  );
  const namedQueriesRef = useRef<Record<string, string>>(report.queries ?? {});
  const [editableRows, setEditableRows] = useState<EditableRow[]>(
    toEditableRows(report.rows),
  );
  const [editableInputs, setEditableInputs] = useState<ReportInput[]>(
    report.inputs ?? [],
  );
  const [inputEditorOpen, setInputEditorOpen] = useState(false);
  const [editingInputIndex, setEditingInputIndex] = useState<number | null>(
    null,
  );
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const toolbarRef = useRef<HTMLDivElement | null>(null);
  const [toolbarHeight, setToolbarHeight] = useState(72);

  // Panel editor dialog state
  const [editorOpen, setEditorOpen] = useState(false);
  const [editingPanelRef, setEditingPanelRef] = useState<{
    rowId: string;
    panelId: string | null; // null = new panel
  } | null>(null);
  const editingPanel = useRef<EditablePanel | null>(null);

  function updateNamedQueries(next: Record<string, string>) {
    namedQueriesRef.current = next;
    setNamedQueries(next);
  }

  function updateNamedQueryDraft(key: string, value: string) {
    namedQueriesRef.current = { ...namedQueriesRef.current, [key]: value };
  }

  useEffect(() => {
    if (toolbarRef.current === null) return undefined;

    const updateToolbarHeight = () => {
      setToolbarHeight(toolbarRef.current?.offsetHeight ?? 72);
    };

    updateToolbarHeight();
    if (typeof ResizeObserver === 'undefined') return undefined;

    const observer = new ResizeObserver(updateToolbarHeight);
    observer.observe(toolbarRef.current);
    return () => observer.disconnect();
  }, []);

  // ---------------------------------------------------------------------------
  // Row operations
  // ---------------------------------------------------------------------------

  function handleQueryRename(oldKey: string, newKey: string) {
    setEditableRows((prev) =>
      prev.map((row) => ({
        ...row,
        panels: row.panels.map((panel) => ({
          ...panel,
          cypher: panel.cypher === oldKey ? newKey : panel.cypher,
          details_cypher:
            panel.details_cypher === oldKey ? newKey : panel.details_cypher,
        })),
      })),
    );
  }

  // ---------------------------------------------------------------------------
  // Input operations
  // ---------------------------------------------------------------------------

  function openAddInput() {
    setEditingInputIndex(null);
    setInputEditorOpen(true);
  }

  function openEditInput(index: number) {
    setEditingInputIndex(index);
    setInputEditorOpen(true);
  }

  function handleInputSave(saved: ReportInput) {
    if (editingInputIndex === null) {
      setEditableInputs((prev) => [...prev, saved]);
    } else {
      setEditableInputs((prev) =>
        prev.map((inp, i) => (i === editingInputIndex ? saved : inp)),
      );
    }
    setInputEditorOpen(false);
  }

  function deleteInput(index: number) {
    setEditableInputs((prev) => prev.filter((_, i) => i !== index));
  }

  function resizeInput(index: number, delta: number) {
    setEditableInputs((prev) =>
      prev.map((inp, i) =>
        i === index
          ? { ...inp, size: Math.max(1, Math.min(12, (inp.size ?? 3) + delta)) }
          : inp,
      ),
    );
  }

  // ---------------------------------------------------------------------------

  function addRow() {
    setEditableRows((prev) => [
      ...prev,
      { _id: uid(), name: 'New Row', panels: [] },
    ]);
  }

  function deleteRow(rowId: string) {
    setEditableRows((prev) => prev.filter((r) => r._id !== rowId));
  }

  function renameRow(rowId: string, name: string) {
    setEditableRows((prev) =>
      prev.map((r) => (r._id === rowId ? { ...r, name } : r)),
    );
  }

  function updateRowProps(
    rowId: string,
    updates: Partial<Pick<EditableRow, 'hide_header' | 'collapsible'>>,
  ) {
    setEditableRows((prev) =>
      prev.map((r) => (r._id === rowId ? { ...r, ...updates } : r)),
    );
  }

  // ---------------------------------------------------------------------------
  // Row reordering via dnd-kit
  // ---------------------------------------------------------------------------

  const rowSensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 8 } }),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    }),
  );

  const handleRowDragEnd = useCallback((event: DragEndEvent) => {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    setEditableRows((prev) => {
      const oldIndex = prev.findIndex((r) => r._id === active.id);
      const newIndex = prev.findIndex((r) => r._id === over.id);
      if (oldIndex === -1 || newIndex === -1) return prev;
      return arrayMove(prev, oldIndex, newIndex);
    });
  }, []);

  // ---------------------------------------------------------------------------
  // Panel operations
  // ---------------------------------------------------------------------------

  function openAddPanel(rowId: string) {
    editingPanel.current = null;
    setEditingPanelRef({ rowId, panelId: null });
    setEditorOpen(true);
  }

  function openEditPanel(rowId: string, panelId: string) {
    const row = editableRows.find((r) => r._id === rowId);
    const panel = row?.panels.find((p) => p._id === panelId);
    if (!panel) return;
    editingPanel.current = panel;
    setEditingPanelRef({ rowId, panelId });
    setEditorOpen(true);
  }

  function handlePanelSave(saved: EditablePanel) {
    if (!editingPanelRef) return;
    const { rowId, panelId } = editingPanelRef;

    if (panelId === null) {
      // Add new
      const newPanel: EditablePanel = { ...saved, _id: uid() };
      setEditableRows((prev) =>
        prev.map((r) =>
          r._id === rowId ? { ...r, panels: [...r.panels, newPanel] } : r,
        ),
      );
    } else {
      // Update existing
      setEditableRows((prev) =>
        prev.map((r) =>
          r._id === rowId
            ? {
                ...r,
                panels: r.panels.map((p) =>
                  p._id === panelId ? { ...saved, _id: panelId } : p,
                ),
              }
            : r,
        ),
      );
    }
    setEditorOpen(false);
  }

  function deletePanel(rowId: string, panelId: string) {
    setEditableRows((prev) =>
      prev.map((r) =>
        r._id === rowId
          ? { ...r, panels: r.panels.filter((p) => p._id !== panelId) }
          : r,
      ),
    );
  }

  function movePanel(panelId: string, fromRowId: string, targetRowId: string) {
    setEditableRows((prev) => {
      const sourceRow = prev.find((r) => r._id === fromRowId);
      const panel = sourceRow?.panels.find((p) => p._id === panelId);
      if (!panel) return prev;
      return prev.map((r) => {
        if (r._id === fromRowId)
          return { ...r, panels: r.panels.filter((p) => p._id !== panelId) };
        if (r._id === targetRowId)
          return { ...r, panels: [...r.panels, panel] };
        return r;
      });
    });
  }

  // ---------------------------------------------------------------------------
  // react-grid-layout: persist x/y/w/h after drag/resize
  // ---------------------------------------------------------------------------

  const handleLayoutChange = useCallback(
    (rowId: string, layouts: ResponsiveLayouts<ResponsiveBreakpoint>) => {
      // The lg layout is the canonical source of truth; xs is a derived,
      // mobile-stacked layout that we don't persist back to the panel config.
      const layout: RglLayout | undefined = layouts.lg;
      if (!layout) return;
      setEditableRows((prev) =>
        prev.map((r) => {
          if (r._id !== rowId) return r;
          let dirty = false;
          const nextPanels = r.panels.map((panel, idx) => {
            const item = layout.find((l) => l.i === String(idx));
            if (!item) return panel;
            if (
              panel.x === item.x &&
              panel.y === item.y &&
              panel.w === item.w &&
              panel.h === item.h
            ) {
              return panel;
            }
            dirty = true;
            return {
              ...panel,
              x: item.x,
              y: item.y,
              w: item.w,
              h: item.h,
            };
          });
          return dirty ? { ...r, panels: nextPanels } : r;
        }),
      );
    },
    [],
  );

  // ---------------------------------------------------------------------------
  // Save
  // ---------------------------------------------------------------------------

  async function handleSave(reportName: string, saveComment: string) {
    setSaving(true);
    setSaveError(null);
    try {
      const updatedReport: Report = {
        ...report,
        name: reportName,
        queries: namedQueriesRef.current,
        inputs: editableInputs.length ? editableInputs : undefined,
        rows: fromEditableRows(editableRows),
      };
      await onSave(updatedReport, saveComment);
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : 'Failed to save');
      setSaving(false);
    }
  }

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  if (hasInvalidRows) {
    return (
      <Container maxWidth={false} sx={{ ...contentContainerSx, py: 3 }}>
        <Alert
          severity="error"
          action={
            <Button color="inherit" size="small" onClick={onCancel}>
              Exit editor
            </Button>
          }
        >
          This report cannot be edited because its configuration is invalid:
          rows must be an array, with panels nested under each row. Refresh the
          page to load the latest version.
        </Alert>
      </Container>
    );
  }

  return (
    <>
      {/* Top toolbar */}
      <EditToolbar
        initialReportName={report.name ?? ''}
        saving={saving}
        saveError={saveError}
        toolbarRef={toolbarRef}
        onCancel={onCancel}
        onSave={handleSave}
      />
      <Box sx={{ height: toolbarHeight }} />

      {/* Named queries section */}
      <Container
        maxWidth={false}
        sx={{ ...contentContainerSx, pt: 1.5, pb: 1 }}
      >
        <Accordion variant="outlined" disableGutters defaultExpanded>
          <AccordionSummary expandIcon={<ExpandMoreIcon />}>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
              <Typography variant="body2" sx={{ fontWeight: 'medium' }}>
                Named Queries
              </Typography>
              <Chip
                label={Object.keys(namedQueries).length}
                size="small"
                variant="outlined"
                sx={{ height: 18, fontSize: '0.7rem' }}
              />
            </Box>
          </AccordionSummary>
          <AccordionDetails>
            <NamedQueryEditor
              queries={namedQueries}
              onChange={updateNamedQueries}
              onDraftValueChange={updateNamedQueryDraft}
              onRename={handleQueryRename}
            />
          </AccordionDetails>
        </Accordion>
      </Container>

      {/* Inputs section */}
      <Container maxWidth={false} sx={{ ...contentContainerSx, pt: 0, pb: 1 }}>
        <Accordion variant="outlined" disableGutters defaultExpanded>
          <AccordionSummary expandIcon={<ExpandMoreIcon />}>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
              <Typography variant="body2" sx={{ fontWeight: 'medium' }}>
                Inputs
              </Typography>
              <Chip
                label={editableInputs.length}
                size="small"
                variant="outlined"
                sx={{ height: 18, fontSize: '0.7rem' }}
              />
            </Box>
          </AccordionSummary>
          <AccordionDetails>
            <Stack spacing={1}>
              {editableInputs.length === 0 && (
                <Typography variant="body2" color="text.secondary">
                  No inputs yet. Add one below to let users filter panel
                  queries.
                </Typography>
              )}
              {editableInputs.map((inp, i) => (
                <InputCard
                  key={i}
                  input={inp}
                  onEdit={() => openEditInput(i)}
                  onDelete={() => deleteInput(i)}
                  onResize={(delta) => resizeInput(i, delta)}
                />
              ))}
              <Box>
                <Button size="small" startIcon={<Add />} onClick={openAddInput}>
                  Add input
                </Button>
              </Box>
            </Stack>
          </AccordionDetails>
        </Accordion>
      </Container>

      {/* Rows with panels */}
      {(() => {
        const rowNameMap = editableRows.map((r) => ({
          id: r._id,
          name: r.name,
        }));
        return (
          <DndContext
            sensors={rowSensors}
            collisionDetection={closestCenter}
            onDragEnd={handleRowDragEnd}
          >
            <SortableContext
              items={editableRows.map((r) => r._id)}
              strategy={verticalListSortingStrategy}
            >
              {editableRows.map((row, rowIndex) => (
                <SortableRowWrapper key={row._id} id={row._id}>
                  {(dragHandleProps) => (
                    <EditableRowCard
                      row={row}
                      rowIndex={rowIndex}
                      onRename={renameRow}
                      onUpdateRowProps={updateRowProps}
                      onAddPanel={openAddPanel}
                      onDeleteRow={deleteRow}
                      onEditPanel={openEditPanel}
                      onDeletePanel={deletePanel}
                      onLayoutChange={handleLayoutChange}
                      dragHandleProps={dragHandleProps}
                      moveTargetRows={rowNameMap.filter(
                        (r) => r.id !== row._id,
                      )}
                      onMovePanel={(panelId, targetRowId) =>
                        movePanel(panelId, row._id, targetRowId)
                      }
                    />
                  )}
                </SortableRowWrapper>
              ))}
            </SortableContext>
          </DndContext>
        );
      })()}

      {/* Add row */}
      <Container maxWidth={false} sx={{ ...contentContainerSx, pb: 2.5 }}>
        <Button
          variant="outlined"
          startIcon={<Add />}
          onClick={addRow}
          fullWidth
        >
          Add Row
        </Button>
      </Container>

      {/* Panel editor dialog */}
      <PanelEditor
        open={editorOpen}
        panel={editingPanelRef?.panelId ? editingPanel.current : null}
        onClose={() => setEditorOpen(false)}
        onSave={handlePanelSave}
        availableVariables={editableInputs.map((i) => ({
          name: i.input_id,
          label: i.label,
        }))}
      />

      {/* Input editor dialog */}
      <InputEditorDialog
        open={inputEditorOpen}
        input={
          editingInputIndex !== null
            ? (editableInputs[editingInputIndex] ?? null)
            : null
        }
        onClose={() => setInputEditorOpen(false)}
        onSave={handleInputSave}
      />
    </>
  );
}

export default EditableReportView;
