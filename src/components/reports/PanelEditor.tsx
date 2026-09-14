import { useState } from 'react';
import {
  Box,
  Button,
  Checkbox,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  FormControl,
  FormControlLabel,
  IconButton,
  InputLabel,
  MenuItem,
  Select,
  Slider,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material';
import Add from '@mui/icons-material/Add';
import DeleteIcon from '@mui/icons-material/Delete';
import {
  Panel,
  PanelParam,
  PanelThreshold,
  ColumnDef,
} from 'src/config.context';
import MarkdownPanelEditor from 'src/components/reports/MarkdownPanelEditor';
import type { MarkdocVariableOption } from 'src/components/MarkdownEditor';
import ThresholdsEditor from 'src/components/reports/ThresholdsEditor';
import { defaultPanelHeight } from 'src/components/reports/panelLayout';
import { migrateLegacyThreshold } from 'src/components/reports/thresholds';

const MAX_HEIGHT_ROWS = 24;
const TYPES_WITH_AUTO_HEIGHT = new Set(['markdown', 'vertical-table']);

const PANEL_TYPES = [
  { value: 'table', label: 'Table' },
  { value: 'vertical-table', label: 'Vertical Table' },
  { value: 'count', label: 'Count' },
  { value: 'bar', label: 'Bar Chart' },
  { value: 'pie', label: 'Pie Chart' },
  { value: 'graph', label: 'Graph' },
  { value: 'progress', label: 'Progress' },
  { value: 'markdown', label: 'Markdown' },
];

const LEGEND_OPTIONS = [
  { value: '', label: 'None' },
  { value: 'row', label: 'Row' },
  { value: 'column', label: 'Column' },
];

export interface EditablePanel extends Panel {
  _id: string;
}

function emptyPanel(type: string): Panel {
  return { type, w: 3 };
}

interface PanelEditorProps {
  open: boolean;
  panel: EditablePanel | null;
  onClose: () => void;
  onSave: (panel: EditablePanel) => void;
  availableVariables?: MarkdocVariableOption[];
}

function ParamRow({
  param,
  onChange,
  onDelete,
}: {
  param: PanelParam;
  onChange: (p: PanelParam) => void;
  onDelete: () => void;
}) {
  return (
    <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
      <TextField
        size="small"
        label="Name"
        value={param.name}
        onChange={(e) => onChange({ ...param, name: e.target.value })}
        sx={{ flex: 1 }}
      />
      <TextField
        size="small"
        label="Value"
        value={param.value ?? ''}
        onChange={(e) =>
          onChange({
            ...param,
            value: e.target.value || undefined,
            input_id: undefined,
          })
        }
        sx={{ flex: 1 }}
      />
      <TextField
        size="small"
        label="Input ID"
        value={param.input_id ?? ''}
        onChange={(e) =>
          onChange({
            ...param,
            input_id: e.target.value || undefined,
            value: undefined,
          })
        }
        sx={{ flex: 1 }}
      />
      <Tooltip title="Remove param">
        <IconButton size="small" onClick={onDelete}>
          <DeleteIcon fontSize="small" />
        </IconButton>
      </Tooltip>
    </Stack>
  );
}

function ColumnRow({
  col,
  onChange,
  onDelete,
}: {
  col: ColumnDef;
  onChange: (c: ColumnDef) => void;
  onDelete: () => void;
}) {
  return (
    <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
      <TextField
        size="small"
        label="Field name"
        value={col.name}
        onChange={(e) => onChange({ ...col, name: e.target.value })}
        sx={{ flex: 1 }}
      />
      <TextField
        size="small"
        label="Display label"
        value={col.label}
        onChange={(e) => onChange({ ...col, label: e.target.value })}
        sx={{ flex: 1 }}
      />
      <Tooltip title="Remove column">
        <IconButton size="small" onClick={onDelete}>
          <DeleteIcon fontSize="small" />
        </IconButton>
      </Tooltip>
    </Stack>
  );
}

/**
 * The form's starting point, computed from the panel being edited.
 *
 * Legacy ``threshold`` is migrated into the ``thresholds`` list so the editor
 * displays equivalent rows; the legacy field is dropped on save (see
 * cleanPanel).
 */
function initialForm(panel: EditablePanel | null): Panel {
  if (!panel) return emptyPanel('count');
  const { _id: _ignored, ...rest } = panel;
  const migrated: Panel = { ...rest };
  if (
    (migrated.thresholds === undefined || migrated.thresholds.length === 0) &&
    migrated.threshold != null &&
    (migrated.type === 'count' || migrated.type === 'progress')
  ) {
    const list = migrateLegacyThreshold(migrated);
    if (list.length > 0) {
      migrated.thresholds = list;
    }
  }
  return migrated;
}

/**
 * The dialog shell. The form is mounted per open and keyed on the panel, so
 * its starting state is *computed* from that panel rather than copied into
 * state by an effect that had to run after the first render had already shown
 * the previous panel's values.
 */
function PanelEditor({
  open,
  panel,
  onClose,
  onSave,
  availableVariables,
}: PanelEditorProps) {
  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
      {open && (
        <PanelEditorForm
          key={panel?._id ?? '__new__'}
          panel={panel}
          onClose={onClose}
          onSave={onSave}
          availableVariables={availableVariables}
        />
      )}
    </Dialog>
  );
}

function PanelEditorForm({
  panel,
  onClose,
  onSave,
  availableVariables,
}: Omit<PanelEditorProps, 'open'>) {
  const [form, setForm] = useState<Panel>(() => initialForm(panel));
  const id = panel?._id ?? '';

  function set<K extends keyof Panel>(key: K, value: Panel[K]) {
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  function handleTypeChange(newType: string) {
    // Keep layout/caption fields, reset type-specific ones. Drop auto_height
    // if the new type doesn't support it (table, charts, etc.) and drop
    // progress_settings unless the new type is also progress.
    setForm({
      type: newType,
      caption: form.caption,
      w: form.w,
      h: form.h,
      x: form.x,
      y: form.y,
      min_h: form.min_h,
      auto_height: TYPES_WITH_AUTO_HEIGHT.has(newType)
        ? form.auto_height
        : undefined,
      progress_settings:
        newType === 'progress' ? form.progress_settings : undefined,
      thresholds:
        newType === 'count' || newType === 'progress'
          ? form.thresholds
          : undefined,
      cypher: newType === 'markdown' ? undefined : form.cypher,
    });
  }

  function handleSave() {
    const cleaned = cleanPanel(form);
    onSave({ ...cleaned, _id: id });
  }

  const isMarkdown = form.type === 'markdown';
  const hasLegend = form.type === 'bar' || form.type === 'pie';
  const hasGraphSettings = form.type === 'graph';
  const hasThreshold = form.type === 'count' || form.type === 'progress';
  const hasProgressSettings = form.type === 'progress';
  const hasColumns = form.type === 'table';
  const hasTableId = form.type === 'vertical-table';
  const hasDetailsQuery =
    form.type === 'table' || form.type === 'vertical-table';

  const params: PanelParam[] = form.params ?? [];
  const columns: ColumnDef[] = form.columns ?? [];

  const legendValue =
    form.type === 'bar'
      ? (form.bar_settings?.legend ?? '')
      : form.type === 'pie'
        ? (form.pie_settings?.legend ?? '')
        : '';

  function setLegend(val: string) {
    const legend = val || undefined;
    if (form.type === 'bar') {
      set('bar_settings', legend ? { legend } : undefined);
    } else if (form.type === 'pie') {
      set('pie_settings', legend ? { legend } : undefined);
    }
  }

  return (
    <>
      <DialogTitle>{panel ? 'Edit Panel' : 'Add Panel'}</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 1 }}>
          {/* Type */}
          <FormControl fullWidth size="small">
            <InputLabel>Panel type</InputLabel>
            <Select
              label="Panel type"
              value={form.type}
              onChange={(e) => handleTypeChange(e.target.value)}
            >
              {PANEL_TYPES.map((t) => (
                <MenuItem key={t.value} value={t.value}>
                  {t.label}
                </MenuItem>
              ))}
            </Select>
          </FormControl>

          {/* Caption */}
          <TextField
            fullWidth
            size="small"
            label="Caption"
            value={form.caption ?? ''}
            onChange={(e) => set('caption', e.target.value || undefined)}
          />
          <FormControlLabel
            control={
              <Checkbox
                size="small"
                checked={form.hide_caption === true}
                onChange={(e) =>
                  set('hide_caption', e.target.checked || undefined)
                }
              />
            }
            label="Hide caption"
          />

          {/* Width */}
          <Box>
            <Typography gutterBottom variant="body2">
              Width (grid columns: {form.w ?? 3})
            </Typography>
            <Slider
              min={1}
              max={12}
              step={1}
              marks
              value={form.w ?? 3}
              onChange={(_, v) => set('w', v as number)}
              valueLabelDisplay="auto"
            />
          </Box>

          {/* Height */}
          <Box>
            <Typography gutterBottom variant="body2">
              Height (grid rows: {form.h ?? defaultPanelHeight(form.type)})
            </Typography>
            <Slider
              min={1}
              max={MAX_HEIGHT_ROWS}
              step={1}
              value={form.h ?? defaultPanelHeight(form.type)}
              onChange={(_, v) => set('h', v as number)}
              valueLabelDisplay="auto"
              disabled={form.auto_height === true}
            />
            {TYPES_WITH_AUTO_HEIGHT.has(form.type) && (
              <FormControlLabel
                control={
                  <Checkbox
                    size="small"
                    checked={form.auto_height === true}
                    onChange={(e) =>
                      set('auto_height', e.target.checked || undefined)
                    }
                  />
                }
                label="Fit content (auto-height)"
              />
            )}
          </Box>

          {/* Cypher (all except markdown) */}
          {!isMarkdown && (
            <>
              <Divider />
              <TextField
                fullWidth
                size="small"
                label="Cypher (query key or direct Cypher)"
                multiline
                minRows={3}
                value={form.cypher ?? ''}
                onChange={(e) => set('cypher', e.target.value || undefined)}
                helperText="Enter a named query key from the Queries section, or a direct Cypher string."
              />
            </>
          )}

          {/* Details cypher for table/vertical-table */}
          {hasDetailsQuery && (
            <TextField
              fullWidth
              size="small"
              label="Details Cypher (query key or direct Cypher)"
              multiline
              minRows={2}
              value={form.details_cypher ?? ''}
              onChange={(e) =>
                set('details_cypher', e.target.value || undefined)
              }
              helperText="Used for the expandable details row."
            />
          )}

          {/* Markdown */}
          {isMarkdown && (
            <>
              <Divider />
              <MarkdownPanelEditor
                value={form.markdown}
                onChange={(v) => set('markdown', v)}
                availableVariables={availableVariables}
              />
            </>
          )}

          {/* Thresholds for count/progress */}
          {hasThreshold && (
            <ThresholdsEditor
              thresholds={form.thresholds ?? []}
              onChange={(next: PanelThreshold[]) => set('thresholds', next)}
              helperText={
                form.type === 'progress'
                  ? 'Each threshold applies when the completion percentage is at or above its value. The highest matching threshold wins.'
                  : 'Each threshold applies when the count is at or above its value. The highest matching threshold wins.'
              }
            />
          )}

          {/* Progress-specific settings */}
          {hasProgressSettings && (
            <FormControlLabel
              control={
                <Checkbox
                  size="small"
                  checked={form.progress_settings?.show_label !== false}
                  onChange={(e) =>
                    set(
                      'progress_settings',
                      e.target.checked
                        ? { ...form.progress_settings, show_label: undefined }
                        : { ...form.progress_settings, show_label: false },
                    )
                  }
                />
              }
              label="Show numerator / denominator label"
            />
          )}

          {/* Legend for bar/pie */}
          {hasLegend && (
            <FormControl size="small" sx={{ width: 200 }}>
              <InputLabel>Legend</InputLabel>
              <Select
                label="Legend"
                value={legendValue}
                onChange={(e) => setLegend(e.target.value)}
              >
                {LEGEND_OPTIONS.map((o) => (
                  <MenuItem key={o.value} value={o.value}>
                    {o.label}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
          )}

          {/* Graph settings */}
          {hasGraphSettings && (
            <>
              <TextField
                size="small"
                label="Node label field"
                value={form.graph_settings?.node_label ?? ''}
                onChange={(e) =>
                  set('graph_settings', {
                    ...form.graph_settings,
                    node_label: e.target.value || undefined,
                  })
                }
                helperText="Node property to display as label (default: label)."
                sx={{ width: 300 }}
              />
              <TextField
                size="small"
                label="Node color-by field"
                value={form.graph_settings?.node_color_by ?? ''}
                onChange={(e) =>
                  set('graph_settings', {
                    ...form.graph_settings,
                    node_color_by: e.target.value || undefined,
                  })
                }
                helperText="Node property to use for color grouping (default: group)."
                sx={{ width: 300 }}
              />
            </>
          )}

          {/* Table ID for vertical-table */}
          {hasTableId && (
            <TextField
              size="small"
              label="Table ID field"
              value={form.table_id ?? ''}
              onChange={(e) => set('table_id', e.target.value || undefined)}
              helperText="The Cypher result field to use as the section caption."
              sx={{ width: 300 }}
            />
          )}

          {/* Params (all non-markdown) */}
          {!isMarkdown && (
            <>
              <Divider />
              <Box>
                <Box
                  sx={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    mb: 1,
                  }}
                >
                  <Typography variant="body2" sx={{ fontWeight: 'medium' }}>
                    Parameters
                  </Typography>
                  <Button
                    size="small"
                    startIcon={<Add />}
                    onClick={() => set('params', [...params, { name: '' }])}
                  >
                    Add param
                  </Button>
                </Box>
                <Stack spacing={1}>
                  {params.map((p, i) => (
                    <ParamRow
                      key={i}
                      param={p}
                      onChange={(updated) => {
                        const next = [...params];
                        next[i] = updated;
                        set('params', next);
                      }}
                      onDelete={() => {
                        const next = params.filter((_, idx) => idx !== i);
                        set('params', next.length ? next : undefined);
                      }}
                    />
                  ))}
                </Stack>
              </Box>
            </>
          )}

          {/* Columns for table */}
          {hasColumns && (
            <>
              <Divider />
              <Box>
                <Box
                  sx={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    mb: 1,
                  }}
                >
                  <Typography variant="body2" sx={{ fontWeight: 'medium' }}>
                    Columns (leave empty to auto-detect)
                  </Typography>
                  <Button
                    size="small"
                    startIcon={<Add />}
                    onClick={() =>
                      set('columns', [...columns, { name: '', label: '' }])
                    }
                  >
                    Add column
                  </Button>
                </Box>
                <Stack spacing={1}>
                  {columns.map((c, i) => (
                    <ColumnRow
                      key={i}
                      col={c}
                      onChange={(updated) => {
                        const next = [...columns];
                        next[i] = updated;
                        set('columns', next);
                      }}
                      onDelete={() => {
                        const next = columns.filter((_, idx) => idx !== i);
                        set('columns', next.length ? next : undefined);
                      }}
                    />
                  ))}
                </Stack>
              </Box>
            </>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" onClick={handleSave}>
          Save Panel
        </Button>
      </DialogActions>
    </>
  );
}

/** Strip undefined/null/empty values before saving. */
function cleanPanel(panel: Panel): Panel {
  const result: Panel = { type: panel.type };
  if (panel.caption) result.caption = panel.caption;
  if (panel.hide_caption) result.hide_caption = true;
  if (panel.w != null) result.w = Math.max(1, Math.min(12, panel.w));
  if (panel.h != null)
    result.h = Math.max(1, Math.min(MAX_HEIGHT_ROWS, panel.h));
  if (panel.x != null) result.x = Math.max(0, panel.x);
  if (panel.y != null) result.y = Math.max(0, panel.y);
  if (panel.min_h != null) result.min_h = Math.max(1, panel.min_h);
  if (panel.auto_height && TYPES_WITH_AUTO_HEIGHT.has(panel.type)) {
    result.auto_height = true;
  }
  if (panel.cypher) result.cypher = panel.cypher;
  if (panel.details_cypher) result.details_cypher = panel.details_cypher;
  if (panel.markdown) result.markdown = panel.markdown;
  if (panel.thresholds && panel.thresholds.length > 0) {
    // Drop in-progress rows with non-finite values (the user added a row but
    // hasn't typed a number yet, or cleared the field). Only persist
    // thresholds with a finite ``value`` and a non-empty ``color``.
    const cleanedThresholds = panel.thresholds
      .map((t) => ({ value: Number(t.value), color: t.color }))
      .filter((t) => Number.isFinite(t.value) && t.color !== '');
    if (cleanedThresholds.length > 0) {
      result.thresholds = cleanedThresholds;
    } else if (panel.threshold != null) {
      result.threshold = panel.threshold;
    }
    // Drop the legacy single ``threshold`` field when a list is set; the
    // renderer prefers ``thresholds`` so keeping both around is just noise.
  } else if (panel.threshold != null) {
    result.threshold = panel.threshold;
  }
  if (panel.table_id) result.table_id = panel.table_id;
  if (panel.bar_settings) result.bar_settings = panel.bar_settings;
  if (panel.pie_settings) result.pie_settings = panel.pie_settings;
  if (panel.graph_settings) result.graph_settings = panel.graph_settings;
  if (panel.progress_settings && panel.progress_settings.show_label === false) {
    result.progress_settings = { show_label: false };
  }
  if (panel.params?.length) result.params = panel.params;
  if (panel.columns?.length) result.columns = panel.columns;
  return result;
}

export default PanelEditor;
