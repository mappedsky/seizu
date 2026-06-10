import {
  Box,
  Chip,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from '@mui/material';
import DetailDialog, {
  DetailSection,
  DetailCodeBlock,
} from 'src/components/DetailDialog';
import { ToolItem, ToolVersion, ToolParamDef } from 'src/hooks/useToolsetsApi';

export type ToolViewData = Pick<
  ToolItem | ToolVersion,
  'name' | 'description' | 'cypher' | 'parameters' | 'enabled'
> & {
  version?: number;
  slug?: string;
  effective_enabled?: boolean | null;
  disabled_reason?: string | null;
};

interface Props {
  open: boolean;
  onClose: () => void;
  data: ToolViewData | null;
}

function ParamTypeChip({ type }: { type: ToolParamDef['type'] }) {
  return (
    <Chip
      label={type}
      size="small"
      variant="outlined"
      sx={{ fontFamily: 'monospace', fontSize: 11 }}
    />
  );
}

export default function ToolDetailDialog({ open, onClose, data }: Props) {
  if (!data) return null;
  const effectiveEnabled = data.effective_enabled ?? data.enabled;
  const statusLabel = effectiveEnabled
    ? 'Enabled'
    : data.disabled_reason === 'toolset_disabled'
      ? 'Disabled by toolset'
      : 'Disabled';

  return (
    <DetailDialog
      open={open}
      onClose={onClose}
      title={data.name}
      secondary={data.version !== undefined ? `v${data.version}` : undefined}
    >
      <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
        {data.slug && (
          <DetailSection title="Slug">
            <Typography variant="body2" sx={{ fontFamily: 'monospace' }}>
              {data.slug}
            </Typography>
          </DetailSection>
        )}

        <DetailSection title="Status">
          <Chip
            label={statusLabel}
            color={effectiveEnabled ? 'success' : 'default'}
            size="small"
          />
        </DetailSection>

        {data.description && (
          <DetailSection title="Description">
            <Typography variant="body2">{data.description}</Typography>
          </DetailSection>
        )}

        <DetailSection title="Cypher">
          <DetailCodeBlock>{data.cypher}</DetailCodeBlock>
        </DetailSection>

        {data.parameters.length > 0 && (
          <DetailSection title="Parameters">
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Name</TableCell>
                  <TableCell>Type</TableCell>
                  <TableCell>Required</TableCell>
                  <TableCell>Default</TableCell>
                  <TableCell>Description</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {data.parameters.map((p, i) => (
                  <TableRow key={i}>
                    <TableCell
                      sx={{ fontFamily: 'monospace', fontWeight: 600 }}
                    >
                      {p.name}
                    </TableCell>
                    <TableCell>
                      <ParamTypeChip type={p.type} />
                    </TableCell>
                    <TableCell sx={{ color: 'text.secondary' }}>
                      {p.required ? 'Yes' : 'No'}
                    </TableCell>
                    <TableCell
                      sx={{ color: 'text.secondary', fontFamily: 'monospace' }}
                    >
                      {p.default !== null && p.default !== undefined
                        ? String(p.default)
                        : '—'}
                    </TableCell>
                    <TableCell sx={{ color: 'text.secondary' }}>
                      {p.description || '—'}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </DetailSection>
        )}
      </Box>
    </DetailDialog>
  );
}
