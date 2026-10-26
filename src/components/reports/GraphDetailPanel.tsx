import {
  Box,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
} from '@mui/material';
import type { GraphNode, GraphLink } from 'src/components/reports/CypherGraph';

// XyFlow internal node properties — exclude from display.
export const GRAPH_INTERNAL_PROPS = new Set([
  'x',
  'y',
  'vx',
  'vy',
  'fx',
  'fy',
  '__indexColor',
  'index',
  // XyFlow node-data keys we add internally
  'original',
  'label',
  'group',
]);

// ─── Item detail panel ────────────────────────────────────────────────────────

interface GraphDetailPanelProps {
  type: 'node' | 'link';
  data: GraphNode | GraphLink;
}

type DetailEntry = [string, unknown];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function detailValue(value: unknown): string {
  if (typeof value === 'object' && value !== null) return JSON.stringify(value);
  return String(value ?? '');
}

function DetailValue({ value }: { value: unknown }) {
  if (Array.isArray(value)) {
    return (
      <Box component="ul" sx={{ m: 0, pl: 2.25 }}>
        {value.map((item, index) => (
          <Box component="li" key={index} sx={{ mb: 0.25 }}>
            <Typography variant="body2" sx={{ wordBreak: 'break-all' }}>
              {detailValue(item)}
            </Typography>
          </Box>
        ))}
      </Box>
    );
  }

  return (
    <Typography variant="body2" sx={{ wordBreak: 'break-all' }}>
      {detailValue(value)}
    </Typography>
  );
}

function displayKey(key: string): string {
  switch (key) {
    case 'neo4j_id':
      return 'Neo4j ID';
    case 'start_node_id':
      return 'Source Neo4j ID';
    case 'end_node_id':
      return 'Target Neo4j ID';
    default:
      return key;
  }
}

function DetailSection({
  title,
  entries,
  emptyText,
  formatMetadataKeys = false,
}: {
  title: string;
  entries: DetailEntry[];
  emptyText?: string;
  formatMetadataKeys?: boolean;
}) {
  if (entries.length === 0 && !emptyText) return null;

  return (
    <Box sx={{ mb: 2 }}>
      <Typography variant="subtitle2" gutterBottom>
        {title}
      </Typography>
      {entries.length === 0 ? (
        <Typography variant="body2" color="text.secondary">
          {emptyText}
        </Typography>
      ) : (
        <TableContainer>
          <Table
            size="small"
            aria-label={`${title} details`}
            sx={{
              tableLayout: 'fixed',
              '& th, & td': {
                borderColor: 'divider',
                px: 0.75,
                py: 0.625,
                verticalAlign: 'top',
              },
            }}
          >
            <TableHead>
              <TableRow>
                <TableCell sx={{ width: '38%' }}>
                  <Typography variant="caption" color="text.secondary">
                    Key
                  </Typography>
                </TableCell>
                <TableCell>
                  <Typography variant="caption" color="text.secondary">
                    Value
                  </Typography>
                </TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {entries.map(([k, v]) => (
                <TableRow key={k}>
                  <TableCell>
                    <Typography
                      variant="caption"
                      color="text.secondary"
                      sx={{ wordBreak: 'break-word' }}
                    >
                      {formatMetadataKeys ? displayKey(k) : k}
                    </Typography>
                  </TableCell>
                  <TableCell>
                    <DetailValue value={v} />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}
    </Box>
  );
}

function metadataEntries(
  type: 'node' | 'link',
  data: GraphNode | GraphLink,
): DetailEntry[] {
  if (type === 'node') {
    const node = data as GraphNode;
    return [
      ...(node.neo4j_id !== undefined
        ? [['neo4j_id', node.neo4j_id] as DetailEntry]
        : [['id', node.id] as DetailEntry]),
      ...(node.labels ? [['labels', node.labels] as DetailEntry] : []),
    ];
  }

  const link = data as GraphLink;
  return [
    ...(link.neo4j_id !== undefined
      ? [['neo4j_id', link.neo4j_id] as DetailEntry]
      : link.id !== undefined
        ? [['id', link.id] as DetailEntry]
        : []),
    ...(link.type !== undefined ? [['type', link.type] as DetailEntry] : []),
    ['source', typeof link.source === 'object' ? link.source.id : link.source],
    ['target', typeof link.target === 'object' ? link.target.id : link.target],
  ];
}

export default function GraphDetailPanel({
  type,
  data,
}: GraphDetailPanelProps) {
  const nestedProperties = isRecord(data.properties) ? data.properties : null;
  const metadataKeys = new Set([
    'id',
    'neo4j_id',
    'labels',
    'source',
    'target',
    'type',
    'properties',
  ]);
  const additionalEntries = Object.entries(data).filter(
    ([k]) => !metadataKeys.has(k) && !GRAPH_INTERNAL_PROPS.has(k),
  );
  const propertyEntries = nestedProperties
    ? Object.entries(nestedProperties)
    : additionalEntries;
  const otherEntries = nestedProperties ? additionalEntries : [];

  return (
    <Box>
      <Typography
        variant="overline"
        color="text.secondary"
        sx={{ display: 'block' }}
        gutterBottom
      >
        {type === 'node' ? 'Node' : 'Relationship'}
      </Typography>
      <DetailSection
        title="Metadata"
        entries={metadataEntries(type, data)}
        formatMetadataKeys
      />
      <DetailSection
        title="Properties"
        entries={propertyEntries}
        emptyText="No properties"
      />
      <DetailSection title="Additional Fields" entries={otherEntries} />
    </Box>
  );
}
