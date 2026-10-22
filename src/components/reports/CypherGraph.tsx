import { memo, useEffect, useMemo, useRef, useState } from 'react';
import {
  Box,
  Card,
  CardContent,
  CardHeader,
  Divider,
  Grid,
  IconButton,
  Tab,
  Tabs,
  Tooltip,
  Typography,
  useMediaQuery,
} from '@mui/material';
import CypherTable from 'src/components/reports/CypherTable';
import { useTheme } from '@mui/material/styles';
import InfoOutlined from '@mui/icons-material/InfoOutlined';
import Info from '@mui/icons-material/Info';
import Error from '@mui/icons-material/Error';
import Add from '@mui/icons-material/Add';
import Remove from '@mui/icons-material/Remove';
import Adjust from '@mui/icons-material/Adjust';
import ZoomInMap from '@mui/icons-material/ZoomInMap';
import ZoomOutMap from '@mui/icons-material/ZoomOutMap';
import {
  ReactFlow,
  Background,
  BackgroundVariant,
  BaseEdge,
  Edge,
  EdgeLabelRenderer,
  EdgeProps,
  Handle,
  MarkerType,
  Node,
  NodeProps,
  Panel,
  Position,
  getStraightPath,
  useEdgesState,
  useNodesState,
  useReactFlow,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import {
  useLazyCypherQuery,
  useLazyHistoryQuery,
} from 'src/hooks/useCypherQuery';
import QueryValidationBadge from 'src/components/reports/QueryValidationBadge';
import { GraphPanelSkeleton } from 'src/components/reports/PanelLoadingSkeletons';
import GraphDetailPanel, {
  GraphSummaryPanel,
} from 'src/components/reports/GraphDetailPanel';
import { chartPalette } from 'src/theme/brand';
import {
  nonSeverityPalette,
  severityColor,
  severityOf,
} from 'src/theme/severity';
import { fontFamilyMono, fontSizeMin } from 'src/theme/typography';

interface GraphSettings {
  node_label?: string;
  node_color_by?: string;
}

export interface GraphNode {
  id: string | number;
  neo4j_id?: string | number;
  labels?: string[];
  properties?: Record<string, unknown>;
  label?: string;
  group?: string;
  [key: string]: unknown;
}

export interface GraphLink {
  id?: string | number;
  neo4j_id?: string | number;
  source: string | number | GraphNode;
  target: string | number | GraphNode;
  type?: string;
  properties?: Record<string, unknown>;
  [key: string]: unknown;
}

interface GraphData {
  nodes: GraphNode[];
  links: GraphLink[];
}

function normalizeGraphData(value: unknown): GraphData | null {
  if (typeof value !== 'object' || value === null) return null;
  const graph = value as Record<string, unknown>;
  if (!Array.isArray(graph['nodes']) || !Array.isArray(graph['links']))
    return null;
  return {
    nodes: graph['nodes'] as GraphNode[],
    links: graph['links'] as GraphLink[],
  };
}

// ─── Neo4j path format ────────────────────────────────────────────────────────

interface Neo4jPathNode {
  id: string | number;
  labels: string[];
  properties: Record<string, unknown>;
}

interface Neo4jPathRelationship {
  id: string | number;
  type: string;
  start_node_id: string | number;
  end_node_id: string | number;
  properties: Record<string, unknown>;
}

interface Neo4jPathValue {
  nodes: Neo4jPathNode[];
  relationships: Neo4jPathRelationship[];
}

function isPathValue(v: unknown): v is Neo4jPathValue {
  if (typeof v !== 'object' || v === null) return false;
  const obj = v as Record<string, unknown>;
  return Array.isArray(obj['nodes']) && Array.isArray(obj['relationships']);
}

function readGraphValue(item: GraphNode | GraphLink, key: string): unknown {
  const properties = item.properties;
  if (
    key !== 'group' &&
    properties &&
    Object.prototype.hasOwnProperty.call(properties, key)
  ) {
    return properties[key];
  }
  if (Object.prototype.hasOwnProperty.call(item, key)) {
    return item[key];
  }
  return properties?.[key];
}

function graphNodeId(node: Neo4jPathNode): string | number {
  const propertyId = node.properties['id'];
  if (typeof propertyId === 'string' && propertyId.length > 0)
    return propertyId;
  if (typeof propertyId === 'number') return propertyId;
  return node.id;
}

export function graphNodeHoverId(
  node: Pick<GraphNode, 'id' | 'neo4j_id' | 'properties'>,
): string {
  const propertyId = node.properties?.['id'];
  if (typeof propertyId === 'string' && propertyId.length > 0)
    return propertyId;
  if (typeof propertyId === 'number') return String(propertyId);
  if (node.neo4j_id !== undefined) return String(node.neo4j_id);
  return String(node.id);
}

/**
 * Given query records, extract a GraphData object.
 *
 * Tries two formats in order:
 *  1. Explicit graph key: first record has a `graph` key with `{nodes, links}` arrays.
 *  2. Path format: any value in any record that has `{nodes, relationships}` — the
 *     backend serialises Neo4j Path objects this way when the query does `RETURN path`.
 *     Nodes are de-duplicated by id across all records.
 */
export function extractGraphData(
  records: Record<string, unknown>[],
  nodeLabelKey: string,
): GraphData | null {
  if (records.length === 0) return null;

  // ── Format 1: explicit graph map (any key whose value has {nodes, links}) ─
  for (const value of Object.values(records[0])) {
    const graph = normalizeGraphData(value);
    if (graph) return graph;
  }

  // ── Format 2: path values across all records ──────────────────────────────
  const nodeMap = new Map<string | number, GraphNode>();
  const nodeIdByNeo4jId = new Map<string | number, string | number>();
  const links: GraphLink[] = [];
  let foundPaths = false;

  for (const record of records) {
    for (const value of Object.values(record)) {
      if (!isPathValue(value)) continue;
      foundPaths = true;
      for (const n of value.nodes) {
        const id = graphNodeId(n);
        nodeIdByNeo4jId.set(n.id, id);
        if (!nodeMap.has(id)) {
          nodeMap.set(id, {
            id,
            neo4j_id: n.id,
            label: String(
              n.properties[nodeLabelKey] ?? n.properties['name'] ?? id,
            ),
            group: n.labels[0] ?? 'default',
            labels: n.labels,
            properties: n.properties,
          });
        }
      }
      for (const rel of value.relationships) {
        links.push({
          neo4j_id: rel.id,
          source: nodeIdByNeo4jId.get(rel.start_node_id) ?? rel.start_node_id,
          target: nodeIdByNeo4jId.get(rel.end_node_id) ?? rel.end_node_id,
          type: rel.type,
          properties: rel.properties,
        });
      }
    }
  }

  if (foundPaths) {
    return { nodes: [...nodeMap.values()], links };
  }

  return null;
}

interface CypherGraphProps {
  cypher?: string;
  params?: Record<string, unknown>;
  caption?: string;
  graphSettings?: GraphSettings;
  needInputs?: string[];
  reportQueryToken?: string;
  queryHistoryId?: string;
  /** Whether the details panel starts open. Default false (collapsed). */
  defaultDetailOpen?: boolean;
  /** Called after a query completes successfully (results received). */
  onQueryComplete?: (historyId: string | null) => void;
  /** When true, the card fills 100% of its parent height instead of using a fixed 450px canvas. */
  fillHeight?: boolean;
  refreshKey?: number;
  onTokenExpired?: () => void;
}

// ─── Colours ─────────────────────────────────────────────────────────────────

// Group → stable index. Color is resolved against a palette at call time so
// the same group keeps its slot across renders while still swapping between
// the light/dark brand palettes when the theme mode changes.
const GROUP_INDEX_MAP = new Map<string, number>();

export function colorForGroup(
  group: string,
  palette: readonly string[] = chartPalette.dark,
): string {
  let index = GROUP_INDEX_MAP.get(group);
  if (index === undefined) {
    index = GROUP_INDEX_MAP.size;
    GROUP_INDEX_MAP.set(group, index);
  }
  return palette[index % palette.length];
}

/**
 * A node's fill: a severity group takes that level's fixed color, so CRITICAL
 * reads the same here as in every other panel; any other group takes the next
 * palette slot, from the hues severity leaves free when the graph has any.
 */
export function nodeColor(
  group: unknown,
  palette: readonly string[],
  mode: 'light' | 'dark',
  severityGraph = false,
): string {
  const severity = severityOf(group);
  if (severity) return severityColor(severity, mode);
  return colorForGroup(
    String(group ?? 'default'),
    severityGraph ? nonSeverityPalette(mode) : palette,
  );
}

// ─── Force layout ─────────────────────────────────────────────────────────────

interface LayoutPoint {
  x: number;
  y: number;
}

interface SegmentDistance {
  distance: number;
  closestX: number;
  closestY: number;
  t: number;
}

export function pointToSegmentDistance(
  point: LayoutPoint,
  start: LayoutPoint,
  end: LayoutPoint,
): SegmentDistance {
  const dx = end.x - start.x;
  const dy = end.y - start.y;
  const lengthSquared = dx * dx + dy * dy;
  if (lengthSquared === 0) {
    const distance = Math.sqrt(
      (point.x - start.x) ** 2 + (point.y - start.y) ** 2,
    );
    return { distance, closestX: start.x, closestY: start.y, t: 0 };
  }

  const rawT =
    ((point.x - start.x) * dx + (point.y - start.y) * dy) / lengthSquared;
  const t = Math.max(0, Math.min(1, rawT));
  const closestX = start.x + t * dx;
  const closestY = start.y + t * dy;
  const distance = Math.sqrt(
    (point.x - closestX) ** 2 + (point.y - closestY) ** 2,
  );
  return { distance, closestX, closestY, t };
}

export function computeLayout(
  nodes: unknown,
  links: unknown,
  width: number,
  height: number,
  repulsion: number = 1,
): Map<string, { x: number; y: number }> {
  const graphNodes = Array.isArray(nodes) ? (nodes as GraphNode[]) : [];
  const graphLinks = Array.isArray(links) ? (links as GraphLink[]) : [];
  if (graphNodes.length === 0) return new Map();

  // SPRING_LEN scales linearly with repulsion; the many-body force scales
  // quadratically so that equilibrium pairwise distances scale roughly linearly.
  const SPRING_LEN = 140 * repulsion;
  const SPRING_K = 0.06;
  const REPULSION = 3000 * repulsion * repulsion;
  const MIN_NODE_DISTANCE = 96 * repulsion;
  const COLLISION_K = 0.18;
  const EDGE_AVOID_DISTANCE = 54;
  const EDGE_AVOID_K = 0.2;
  const DAMPING = 0.82;

  const positions = new Map<
    string,
    { x: number; y: number; vx: number; vy: number }
  >();
  const cx = width / 2;
  const cy = height / 2;
  const r = Math.min(width, height) * 0.32;
  graphNodes.forEach((n, i) => {
    const angle = (i / Math.max(graphNodes.length, 1)) * 2 * Math.PI;
    positions.set(String(n.id), {
      x: cx + r * Math.cos(angle),
      y: cy + r * Math.sin(angle),
      vx: 0,
      vy: 0,
    });
  });

  const nodeIds = graphNodes.map((n) => String(n.id));
  const edgePairs = graphLinks.map((l) => ({
    source: String(
      typeof l.source === 'object' ? (l.source as GraphNode).id : l.source,
    ),
    target: String(
      typeof l.target === 'object' ? (l.target as GraphNode).id : l.target,
    ),
  }));
  const edgeKeys = new Set<string>();
  const adjacency = new Map<string, Set<string>>();
  nodeIds.forEach((id) => adjacency.set(id, new Set()));
  edgePairs.forEach(({ source, target }) => {
    edgeKeys.add(
      source < target ? `${source}\0${target}` : `${target}\0${source}`,
    );
    adjacency.get(source)?.add(target);
    adjacency.get(target)?.add(source);
  });

  const componentByNode = new Map<string, number>();
  let nextComponent = 0;
  nodeIds.forEach((id) => {
    if (componentByNode.has(id)) return;
    const stack = [id];
    const component = nextComponent;
    nextComponent += 1;
    componentByNode.set(id, component);
    while (stack.length > 0) {
      const current = stack.pop()!;
      adjacency.get(current)?.forEach((neighbor) => {
        if (componentByNode.has(neighbor)) return;
        componentByNode.set(neighbor, component);
        stack.push(neighbor);
      });
    }
  });

  for (let iter = 0; iter < 150; iter++) {
    const alpha = 1 - iter / 150;

    nodeIds.forEach((id) => {
      const p = positions.get(id)!;
      p.vx = 0;
      p.vy = 0;
    });

    for (let i = 0; i < nodeIds.length; i++) {
      for (let j = i + 1; j < nodeIds.length; j++) {
        const a = positions.get(nodeIds[i])!;
        const b = positions.get(nodeIds[j])!;
        const dx = b.x - a.x;
        const dy = b.y - a.y;
        const d2 = Math.max(dx * dx + dy * dy, 1);
        const d = Math.sqrt(d2);
        const edgeKey =
          nodeIds[i] < nodeIds[j]
            ? `${nodeIds[i]}\0${nodeIds[j]}`
            : `${nodeIds[j]}\0${nodeIds[i]}`;
        const directlyConnected = edgeKeys.has(edgeKey);
        const sameComponent =
          componentByNode.get(nodeIds[i]) === componentByNode.get(nodeIds[j]);
        const repulsionMultiplier = directlyConnected
          ? 1
          : sameComponent
            ? 2.4
            : 5.5;
        const collisionForce =
          d < MIN_NODE_DISTANCE
            ? (MIN_NODE_DISTANCE - d) * COLLISION_K * alpha
            : 0;
        const f =
          (REPULSION * repulsionMultiplier * alpha) / d2 + collisionForce;
        a.vx -= (dx / d) * f;
        a.vy -= (dy / d) * f;
        b.vx += (dx / d) * f;
        b.vy += (dy / d) * f;
      }
    }

    edgePairs.forEach(({ source, target }) => {
      const a = positions.get(source);
      const b = positions.get(target);
      if (!a || !b) return;
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const d = Math.max(Math.sqrt(dx * dx + dy * dy), 1);
      const f = SPRING_K * (d - SPRING_LEN) * alpha;
      a.vx += (dx / d) * f;
      a.vy += (dy / d) * f;
      b.vx -= (dx / d) * f;
      b.vy -= (dy / d) * f;
    });

    edgePairs.forEach(({ source, target }) => {
      const a = positions.get(source);
      const b = positions.get(target);
      if (!a || !b) return;

      const edgeDx = b.x - a.x;
      const edgeDy = b.y - a.y;
      const edgeDistance = Math.max(
        Math.sqrt(edgeDx * edgeDx + edgeDy * edgeDy),
        1,
      );
      nodeIds.forEach((id) => {
        if (id === source || id === target) return;
        const p = positions.get(id)!;
        const segment = pointToSegmentDistance(p, a, b);
        if (
          segment.t <= 0.08 ||
          segment.t >= 0.92 ||
          segment.distance >= EDGE_AVOID_DISTANCE
        )
          return;

        let nx = p.x - segment.closestX;
        let ny = p.y - segment.closestY;
        const normalDistance = Math.sqrt(nx * nx + ny * ny);
        if (normalDistance > 1) {
          nx /= normalDistance;
          ny /= normalDistance;
        } else {
          nx = -edgeDy / edgeDistance;
          ny = edgeDx / edgeDistance;
          const awayFromCenterX = p.x - cx;
          const awayFromCenterY = p.y - cy;
          if (nx * awayFromCenterX + ny * awayFromCenterY < 0) {
            nx *= -1;
            ny *= -1;
          }
        }

        const f =
          (EDGE_AVOID_DISTANCE - segment.distance) * EDGE_AVOID_K * alpha;
        p.vx += nx * f;
        p.vy += ny * f;
      });
    });

    nodeIds.forEach((id) => {
      const p = positions.get(id)!;
      p.x += p.vx * DAMPING;
      p.y += p.vy * DAMPING;
    });
  }

  const result = new Map<string, { x: number; y: number }>();
  positions.forEach((p, id) => result.set(id, { x: p.x, y: p.y }));
  return result;
}

// ─── Custom node ──────────────────────────────────────────────────────────────

type HandleSide =
  | 'right'
  | 'right-lower'
  | 'bottom-right'
  | 'bottom-right-lower'
  | 'bottom'
  | 'bottom-left-lower'
  | 'bottom-left'
  | 'left-lower'
  | 'left'
  | 'left-upper'
  | 'top-left'
  | 'top-left-upper'
  | 'top'
  | 'top-right-upper'
  | 'top-right'
  | 'right-upper';

const HANDLE_SIDES: HandleSide[] = [
  'right',
  'right-lower',
  'bottom-right',
  'bottom-right-lower',
  'bottom',
  'bottom-left-lower',
  'bottom-left',
  'left-lower',
  'left',
  'left-upper',
  'top-left',
  'top-left-upper',
  'top',
  'top-right-upper',
  'top-right',
  'right-upper',
];

const HANDLE_DEFINITIONS: Record<
  HandleSide,
  { position: Position; style: React.CSSProperties }
> = {
  right: { position: Position.Right, style: { top: '50%' } },
  'right-lower': { position: Position.Right, style: { top: '70%' } },
  'bottom-right': { position: Position.Bottom, style: { left: '78%' } },
  'bottom-right-lower': { position: Position.Bottom, style: { left: '65%' } },
  bottom: { position: Position.Bottom, style: { left: '50%' } },
  'bottom-left-lower': { position: Position.Bottom, style: { left: '35%' } },
  'bottom-left': { position: Position.Bottom, style: { left: '22%' } },
  'left-lower': { position: Position.Left, style: { top: '70%' } },
  left: { position: Position.Left, style: { top: '50%' } },
  'left-upper': { position: Position.Left, style: { top: '30%' } },
  'top-left': { position: Position.Top, style: { left: '22%' } },
  'top-left-upper': { position: Position.Top, style: { left: '35%' } },
  top: { position: Position.Top, style: { left: '50%' } },
  'top-right-upper': { position: Position.Top, style: { left: '65%' } },
  'top-right': { position: Position.Top, style: { left: '78%' } },
  'right-upper': { position: Position.Right, style: { top: '30%' } },
};

const hiddenHandleStyle: React.CSSProperties = {
  opacity: 0,
  width: 1,
  height: 1,
  minWidth: 1,
  minHeight: 1,
  border: 0,
  background: 'transparent',
  pointerEvents: 'none',
};

function GraphNodeComponent({ data, selected }: NodeProps) {
  const theme = useTheme();
  const palette =
    theme.palette.mode === 'dark' ? chartPalette.dark : chartPalette.light;
  const color = nodeColor(
    data['group'],
    palette,
    theme.palette.mode,
    Boolean(data['severityGraph']),
  );
  const typeLabel = String(data['group'] ?? data['label'] ?? data['id'] ?? '');
  const hoverId = graphNodeHoverId(data as unknown as GraphNode);
  const title = typeLabel ? `${typeLabel}: ${hoverId}` : hoverId;

  return (
    <Tooltip
      title={title}
      arrow
      disableInteractive
      enterDelay={NODE_HOVER_ENTER_DELAY_MS}
      enterNextDelay={0}
      placement="top"
    >
      <div style={{ cursor: 'pointer' }}>
        {HANDLE_SIDES.map((side) => {
          const handle = HANDLE_DEFINITIONS[side];
          return (
            <Handle
              key={`target-${side}`}
              id={`target-${side}`}
              type="target"
              position={handle.position}
              style={{ ...hiddenHandleStyle, ...handle.style }}
            />
          );
        })}
        <div
          style={{
            // Wide enough that a label at the type floor (a severity, a
            // category) fits on a line rather than breaking mid-word.
            width: NODE_DIAMETER,
            height: NODE_DIAMETER,
            borderRadius: '50%',
            background: color,
            border: `2.5px solid ${selected ? theme.palette.primary.main : theme.palette.background.paper}`,
            boxSizing: 'border-box',
            boxShadow: selected
              ? `0 0 0 2px ${theme.palette.primary.main}`
              : 'none',
            color: theme.palette.getContrastText(color),
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontFamily: theme.typography.fontFamily ?? 'sans-serif',
            fontSize: fontSizeMin,
            fontWeight: 600,
            lineHeight: 1.15,
            overflow: 'hidden',
            padding: 6,
            textTransform: 'none',
          }}
        >
          <span
            style={{
              display: '-webkit-box',
              WebkitBoxOrient: 'vertical',
              WebkitLineClamp: 3,
              maxWidth: '100%',
              minWidth: 0,
              overflow: 'hidden',
              overflowWrap: 'anywhere',
              textAlign: 'center',
              textOverflow: 'ellipsis',
              whiteSpace: 'normal',
            }}
          >
            {typeLabel}
          </span>
        </div>
        {HANDLE_SIDES.map((side) => {
          const handle = HANDLE_DEFINITIONS[side];
          return (
            <Handle
              key={`source-${side}`}
              id={`source-${side}`}
              type="source"
              position={handle.position}
              style={{ ...hiddenHandleStyle, ...handle.style }}
            />
          );
        })}
      </div>
    </Tooltip>
  );
}

const nodeTypes = { graphNode: GraphNodeComponent };

const NODE_HOVER_ENTER_DELAY_MS = 150;

const NODE_DIAMETER = 72;

// The type floor (fontSizeMin), in px: the offset below is arithmetic on it.
const RELATIONSHIP_LABEL_FONT_SIZE = 11;
const RELATIONSHIP_LABEL_GAP = 2;
const RELATIONSHIP_LABEL_OFFSET =
  RELATIONSHIP_LABEL_FONT_SIZE / 2 + RELATIONSHIP_LABEL_GAP;

export function relationshipLabelTransform(
  sourceX: number,
  sourceY: number,
  targetX: number,
  targetY: number,
  labelX: number,
  labelY: number,
): string {
  const rawAngle =
    (Math.atan2(targetY - sourceY, targetX - sourceX) * 180) / Math.PI;
  let readableAngle =
    rawAngle > 90 || rawAngle < -90 ? rawAngle + 180 : rawAngle;
  if (readableAngle >= 360) readableAngle -= 360;
  if (readableAngle <= -360) readableAngle += 360;

  const dx = targetX - sourceX;
  const dy = targetY - sourceY;
  const distance = Math.max(Math.sqrt(dx * dx + dy * dy), 1);
  let offsetX = (dy / distance) * RELATIONSHIP_LABEL_OFFSET;
  let offsetY = (-dx / distance) * RELATIONSHIP_LABEL_OFFSET;
  if (offsetY > 0) {
    offsetX *= -1;
    offsetY *= -1;
  }

  return `translate(-50%, -50%) translate(${labelX + offsetX}px, ${labelY + offsetY}px) rotate(${readableAngle}deg)`;
}

function RelationshipEdge(
  props: EdgeProps & { labelStyle?: React.CSSProperties },
) {
  const {
    sourceX,
    sourceY,
    targetX,
    targetY,
    markerEnd,
    style,
    label,
    labelStyle,
    data,
    selected,
  } = props;
  // A label with no clear place waits for its edge, or a node on it, to be
  // selected; everything else is dimmed by then, so it reads clearly.
  const showLabel =
    Boolean(label) && (!data?.labelHidden || data?.focused || selected);
  const [edgePath] = getStraightPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
  });
  // Where along the edge the label sits, chosen by placeEdgeLabels so it
  // clears its neighbours; the midpoint when nothing chose.
  const labelT = typeof data?.labelT === 'number' ? data.labelT : 0.5;
  const labelX = sourceX + (targetX - sourceX) * labelT;
  const labelY = sourceY + (targetY - sourceY) * labelT;
  const labelColor =
    (labelStyle as React.CSSProperties | undefined)?.color ??
    (labelStyle as React.CSSProperties | undefined)?.fill ??
    style?.stroke ??
    'currentColor';
  const labelOpacity =
    (labelStyle as React.CSSProperties | undefined)?.opacity ??
    style?.opacity ??
    1;

  return (
    <>
      <BaseEdge path={edgePath} markerEnd={markerEnd} style={style} />
      {showLabel ? (
        <EdgeLabelRenderer>
          <div
            style={{
              position: 'absolute',
              transform: relationshipLabelTransform(
                sourceX,
                sourceY,
                targetX,
                targetY,
                labelX,
                labelY,
              ),
              transformOrigin: 'center',
              pointerEvents: 'none',
              color: String(labelColor),
              opacity: Number(labelOpacity),
              fontSize: RELATIONSHIP_LABEL_FONT_SIZE,
              fontWeight: 500,
              lineHeight: 1,
              whiteSpace: 'nowrap',
              textShadow:
                '0 0 2px var(--xy-edge-label-background-color, transparent)',
            }}
          >
            {label}
          </div>
        </EdgeLabelRenderer>
      ) : null}
    </>
  );
}

const edgeTypes = { relationship: RelationshipEdge };

// ─── Converters ───────────────────────────────────────────────────────────────

export function closestEdgeHandles(
  sourcePosition: { x: number; y: number },
  targetPosition: { x: number; y: number },
): { sourceHandle: string; targetHandle: string } {
  const dx = targetPosition.x - sourcePosition.x;
  const dy = targetPosition.y - sourcePosition.y;
  const sector = Math.round(Math.atan2(dy, dx) / (Math.PI / 8));
  const sourceIndex = (sector + HANDLE_SIDES.length) % HANDLE_SIDES.length;
  const targetIndex = (sourceIndex + 8) % HANDLE_SIDES.length;
  const sourceSide = HANDLE_SIDES[sourceIndex];
  const targetSide = HANDLE_SIDES[targetIndex];

  return {
    sourceHandle: `source-${sourceSide}`,
    targetHandle: `target-${targetSide}`,
  };
}

function buildXyNodes(
  graphNodes: GraphNode[],
  positions: Map<string, { x: number; y: number }>,
  nodeLabelKey: string,
  nodeColorByKey: string,
): Node[] {
  const severityGraph = graphNodes.some((n) =>
    severityOf(readGraphValue(n, nodeColorByKey)),
  );
  return graphNodes.map((n) => {
    const pos = positions.get(String(n.id)) ?? { x: 0, y: 0 };
    return {
      id: String(n.id),
      type: 'graphNode',
      position: pos,
      data: {
        ...n,
        label: String(readGraphValue(n, nodeLabelKey) ?? n.id ?? ''),
        group: String(readGraphValue(n, nodeColorByKey) ?? 'default'),
        severityGraph,
        original: n,
      },
    };
  });
}

/** Positions along an edge a label may take, best first. */
const LABEL_POSITIONS = [0.5, 0.42, 0.58, 0.36, 0.64, 0.3, 0.7];
/** Rough advance of one character of a relationship label, in px. */
const LABEL_CHAR_WIDTH = RELATIONSHIP_LABEL_FONT_SIZE * 0.58;

interface LabelBox {
  left: number;
  right: number;
  top: number;
  bottom: number;
}

function boxOverlap(a: LabelBox, b: LabelBox): number {
  const x = Math.min(a.right, b.right) - Math.max(a.left, b.left);
  const y = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
  return x > 0 && y > 0 ? x * y : 0;
}

/**
 * The relationship label for each edge, where along it the label sits, and
 * whether it waits for the edge to be selected.
 *
 * Edges between the same two nodes are drawn over each other, so only the
 * first carries a label, naming every relationship between the pair (`REST ×2`,
 * `reads · writes`). Labels are then placed shortest edge first, since a short
 * edge has the fewest places to put one: each takes the first position along
 * its edge that clears the labels already placed and the nodes. A label with
 * no clear position is shown only while its edge is selected, rather than
 * printed over another.
 */
export function placeEdgeLabels(
  graphLinks: GraphLink[],
  positions: Map<string, { x: number; y: number }>,
): Array<{ label: string | undefined; labelT: number; labelHidden: boolean }> {
  const endpoints = graphLinks.map((l) => [
    String(
      typeof l.source === 'object' ? (l.source as GraphNode).id : l.source,
    ),
    String(
      typeof l.target === 'object' ? (l.target as GraphNode).id : l.target,
    ),
  ]);
  const pairKey = ([a, b]: string[]) =>
    a < b ? `${a}\u0000${b}` : `${b}\u0000${a}`;

  const pairLabels = new Map<string, string[]>();
  const pairOwner = new Map<string, number>();
  endpoints.forEach((ends, i) => {
    const key = pairKey(ends);
    if (!pairOwner.has(key)) pairOwner.set(key, i);
    const type = graphLinks[i].type;
    if (type) pairLabels.set(key, [...(pairLabels.get(key) ?? []), type]);
  });
  const labelFor = (i: number): string | undefined => {
    const key = pairKey(endpoints[i]);
    if (pairOwner.get(key) !== i) return undefined;
    const counts = new Map<string, number>();
    (pairLabels.get(key) ?? []).forEach((t) =>
      counts.set(t, (counts.get(t) ?? 0) + 1),
    );
    if (counts.size === 0) return undefined;
    return [...counts].map(([t, n]) => (n > 1 ? `${t} ×${n}` : t)).join(' · ');
  };

  const radius = NODE_DIAMETER / 2;
  const centre = (id: string) => {
    const p = positions.get(id);
    return p ? { x: p.x + radius, y: p.y + radius } : undefined;
  };
  const nodeBoxes: LabelBox[] = [...positions.values()].map((p) => ({
    left: p.x,
    right: p.x + NODE_DIAMETER,
    top: p.y,
    bottom: p.y + NODE_DIAMETER,
  }));

  const results: Array<{
    label: string | undefined;
    labelT: number;
    labelHidden: boolean;
  }> = endpoints.map((_, i) => ({
    label: labelFor(i),
    labelT: 0.5,
    labelHidden: false,
  }));
  const lengthOf = (i: number) => {
    const from = centre(endpoints[i][0]);
    const to = centre(endpoints[i][1]);
    return from && to ? Math.hypot(to.x - from.x, to.y - from.y) : 0;
  };
  const order = results
    .map((_, i) => i)
    .filter((i) => results[i].label !== undefined)
    .sort((a, b) => lengthOf(a) - lengthOf(b));

  const placed: LabelBox[] = [];
  for (const i of order) {
    const label = results[i].label!;
    const from = centre(endpoints[i][0]);
    const to = centre(endpoints[i][1]);
    if (!from || !to) continue;

    const dx = to.x - from.x;
    const dy = to.y - from.y;
    const length = Math.max(Math.hypot(dx, dy), 1);
    const cos = Math.abs(dx / length);
    const sin = Math.abs(dy / length);
    const width = label.length * LABEL_CHAR_WIDTH + 4;
    const height = RELATIONSHIP_LABEL_FONT_SIZE + 4;
    // The label's footprint once rotated onto the edge, as an upright box.
    const halfW = (width * cos + height * sin) / 2;
    const halfH = (width * sin + height * cos) / 2;

    let best:
      | { t: number; labels: number; nodes: number; box: LabelBox }
      | undefined;
    for (const t of LABEL_POSITIONS) {
      const x = from.x + dx * t;
      const y = from.y + dy * t;
      const box = {
        left: x - halfW,
        right: x + halfW,
        top: y - halfH,
        bottom: y + halfH,
      };
      const labels = placed.reduce((sum, o) => sum + boxOverlap(box, o), 0);
      const nodes = nodeBoxes.reduce((sum, n) => sum + boxOverlap(box, n), 0);
      if (
        !best ||
        labels < best.labels ||
        (labels === best.labels && nodes < best.nodes)
      ) {
        best = { t, labels, nodes, box };
      }
      if (labels === 0 && nodes === 0) break;
    }
    if (!best) continue;
    results[i].labelT = best.t;
    if (best.labels > 0) {
      results[i].labelHidden = true;
    } else {
      placed.push(best.box);
    }
  }
  return results;
}

export function buildXyEdges(
  graphLinks: GraphLink[],
  edgeColor: string,
  positions?: Map<string, { x: number; y: number }>,
): Edge[] {
  const labels = positions
    ? placeEdgeLabels(graphLinks, positions)
    : graphLinks.map((l) => ({
        label: l.type ?? undefined,
        labelT: 0.5,
        labelHidden: false,
      }));
  return graphLinks.map((l, i) => {
    const source = String(
      typeof l.source === 'object' ? (l.source as GraphNode).id : l.source,
    );
    const target = String(
      typeof l.target === 'object' ? (l.target as GraphNode).id : l.target,
    );
    const handles =
      positions?.has(source) && positions.has(target)
        ? closestEdgeHandles(positions.get(source)!, positions.get(target)!)
        : {};
    return {
      id: `edge-${i}`,
      type: 'relationship',
      source,
      target,
      ...handles,
      label: labels[i].label,
      labelShowBg: false,
      labelStyle: {
        fontSize: RELATIONSHIP_LABEL_FONT_SIZE,
        color: edgeColor,
        fill: edgeColor,
        fontWeight: 500,
        opacity: FULL_OPACITY,
      },
      style: { stroke: edgeColor, strokeWidth: 1.4, opacity: FULL_OPACITY },
      markerEnd: { type: MarkerType.ArrowClosed, color: edgeColor },
      data: {
        original: l,
        labelT: labels[i].labelT,
        labelHidden: labels[i].labelHidden,
      },
    };
  });
}

// ─── Focus/dim helper ────────────────────────────────────────────────────────

export function applyFocusOpacity(
  nodes: Node[],
  edges: Edge[],
  focusedId: string | null,
  edgeColor: string,
): { nodes: Node[]; edges: Edge[] } {
  const transition = 'opacity 0.2s';

  if (focusedId === null) {
    return {
      nodes: nodes.map((n) => ({
        ...n,
        style: { ...n.style, opacity: FULL_OPACITY, transition },
      })),
      edges: edges.map((e) => ({
        ...e,
        data: { ...e.data, focused: false },
        style: {
          ...e.style,
          stroke: edgeColor,
          opacity: FULL_OPACITY,
          transition,
        },
        labelStyle: { ...e.labelStyle, opacity: FULL_OPACITY },
        labelBgStyle: { ...e.labelBgStyle, opacity: FULL_OPACITY },
      })),
    };
  }

  const isNode = nodes.some((n) => n.id === focusedId);

  if (isNode) {
    const connectedEdgeIds = new Set<string>();
    const neighborNodeIds = new Set<string>([focusedId]);
    for (const e of edges) {
      if (e.source === focusedId || e.target === focusedId) {
        connectedEdgeIds.add(e.id);
        neighborNodeIds.add(e.source === focusedId ? e.target : e.source);
      }
    }
    return {
      nodes: nodes.map((n) => ({
        ...n,
        style: {
          ...n.style,
          opacity: neighborNodeIds.has(n.id) ? FULL_OPACITY : DIM_OPACITY,
          transition,
        },
      })),
      edges: edges.map((e) => ({
        ...e,
        data: { ...e.data, focused: connectedEdgeIds.has(e.id) },
        style: {
          ...e.style,
          stroke: edgeColor,
          opacity: connectedEdgeIds.has(e.id) ? FULL_OPACITY : DIM_OPACITY,
          transition,
        },
        labelStyle: {
          ...e.labelStyle,
          opacity: connectedEdgeIds.has(e.id) ? FULL_OPACITY : DIM_OPACITY,
        },
        labelBgStyle: {
          ...e.labelBgStyle,
          opacity: connectedEdgeIds.has(e.id) ? FULL_OPACITY : DIM_OPACITY,
        },
      })),
    };
  }

  // Edge focused
  const focusedEdge = edges.find((e) => e.id === focusedId);
  if (!focusedEdge) return { nodes, edges };
  const litNodeIds = new Set([focusedEdge.source, focusedEdge.target]);
  return {
    nodes: nodes.map((n) => ({
      ...n,
      style: {
        ...n.style,
        opacity: litNodeIds.has(n.id) ? FULL_OPACITY : DIM_OPACITY,
        transition,
      },
    })),
    edges: edges.map((e) => ({
      ...e,
      data: { ...e.data, focused: e.id === focusedId },
      style: {
        ...e.style,
        stroke: edgeColor,
        opacity: e.id === focusedId ? FULL_OPACITY : DIM_OPACITY,
        transition,
      },
      labelStyle: {
        ...e.labelStyle,
        opacity: e.id === focusedId ? FULL_OPACITY : DIM_OPACITY,
      },
      labelBgStyle: {
        ...e.labelBgStyle,
        opacity: e.id === focusedId ? FULL_OPACITY : DIM_OPACITY,
      },
    })),
  };
}

// ─── Auto-fit helper ─────────────────────────────────────────────────────────
// Rendered inside <ReactFlow> so useReactFlow() is in context.

/**
 * Fits the view with a smooth transition whenever `trigger` changes identity —
 * every layout rebuild — skipping the initial mount.
 */
function AutoFitEffect({ trigger }: { trigger: unknown }) {
  const { fitView } = useReactFlow();
  const isFirstRun = useRef(true);
  useEffect(() => {
    if (isFirstRun.current) {
      isFirstRun.current = false;
      return;
    }
    fitView({ padding: 0.2, duration: 200 });
  }, [trigger, fitView]);
  return null;
}

// ─── Graph controls panel ────────────────────────────────────────────────────
// Single panel at bottom-left: zoom/fit buttons, then a divider, then
// repulsion +/- buttons — all in one unified box.
// Uses a plain custom button (CtrlBtn) instead of ReactFlow's ControlButton so
// we fully own the icon sizing without fighting ReactFlow's stylesheet.

const REPULSION_MIN = 0.5;
const REPULSION_MAX = 4;
const REPULSION_STEP = 0.25;
const ICON_SIZE = 20;
const DIM_OPACITY = 0.35;
const FULL_OPACITY = 1;

function CtrlBtn({
  title,
  onClick,
  disabled = false,
  children,
}: {
  title: string;
  onClick: () => void;
  disabled?: boolean;
  children: React.ReactNode;
}) {
  return (
    <Box
      component="button"
      type="button"
      title={title}
      onClick={!disabled ? onClick : undefined}
      sx={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        width: 36,
        height: 36,
        border: 'none',
        borderBottom: '1px solid',
        borderColor: 'divider',
        bgcolor: 'background.paper',
        color: 'text.primary',
        cursor: disabled ? 'default' : 'pointer',
        opacity: disabled ? 0.4 : 1,
        p: 0,
        '&:hover': { bgcolor: disabled ? 'background.paper' : 'action.hover' },
        '&:last-child': { borderBottom: 'none' },
      }}
    >
      {children}
    </Box>
  );
}

interface GraphControlsProps {
  repulsion: number;
  onRepulsionChange: (v: number) => void;
}

function GraphControls({ repulsion, onRepulsionChange }: GraphControlsProps) {
  const { zoomIn, zoomOut, fitView } = useReactFlow();

  return (
    <Panel position="bottom-left">
      <Box
        sx={{
          display: 'flex',
          flexDirection: 'column',
          borderRadius: '2px',
          overflow: 'hidden',
          bgcolor: 'background.paper',
          boxShadow: 2,
        }}
      >
        <CtrlBtn title="Zoom in" onClick={() => zoomIn()}>
          <Add style={{ width: ICON_SIZE, height: ICON_SIZE }} />
        </CtrlBtn>
        <CtrlBtn title="Zoom out" onClick={() => zoomOut()}>
          <Remove style={{ width: ICON_SIZE, height: ICON_SIZE }} />
        </CtrlBtn>
        <CtrlBtn title="Fit view" onClick={() => fitView({ padding: 0.2 })}>
          <Adjust style={{ width: ICON_SIZE, height: ICON_SIZE }} />
        </CtrlBtn>
        <Box sx={{ height: '2px', bgcolor: 'divider' }} />
        <CtrlBtn
          title="Increase repulsion"
          onClick={() =>
            onRepulsionChange(
              Math.min(REPULSION_MAX, repulsion + REPULSION_STEP),
            )
          }
          disabled={repulsion >= REPULSION_MAX}
        >
          <ZoomOutMap style={{ width: ICON_SIZE, height: ICON_SIZE }} />
        </CtrlBtn>
        <CtrlBtn
          title="Decrease repulsion"
          onClick={() =>
            onRepulsionChange(
              Math.max(REPULSION_MIN, repulsion - REPULSION_STEP),
            )
          }
          disabled={repulsion <= REPULSION_MIN}
        >
          <ZoomInMap style={{ width: ICON_SIZE, height: ICON_SIZE }} />
        </CtrlBtn>
      </Box>
    </Panel>
  );
}

// ─── Main component ───────────────────────────────────────────────────────────

const DETAIL_PANEL_WIDTH = 280;

function CypherGraph({
  cypher,
  params,
  caption,
  graphSettings,
  needInputs,
  reportQueryToken,
  queryHistoryId,
  defaultDetailOpen = false,
  onQueryComplete,
  fillHeight = false,
  refreshKey,
  onTokenExpired,
}: CypherGraphProps) {
  const theme = useTheme();

  const [runAdhocQuery, adhocState] = useLazyCypherQuery(
    queryHistoryId ? undefined : cypher,
    queryHistoryId ? undefined : reportQueryToken,
  );
  const [runHistoryQuery, historyQueryState] = useLazyHistoryQuery();

  const activeState = queryHistoryId ? historyQueryState : adhocState;
  const { loading, error, records, warnings, queryErrors, tokenExpired } =
    activeState;
  const completedHistoryId = activeState.historyId;

  // Call onQueryComplete once after each successful query (loading → false with records).
  const prevLoadingRef = useRef(false);
  useEffect(() => {
    if (
      prevLoadingRef.current &&
      !loading &&
      records !== undefined &&
      onQueryComplete
    ) {
      onQueryComplete(completedHistoryId);
    }
    prevLoadingRef.current = loading;
  }, [loading, records, onQueryComplete, completedHistoryId]);

  // Selection and tab preference belong to the query they were made against:
  // a new query drops them by being read past, rather than through an effect
  // that clears three states one render after the new results are on screen.
  const querySignature = `${cypher ?? ''}\u0000${queryHistoryId ?? ''}`;
  const [selection, setSelection] = useState<{
    query: string;
    item:
      | { type: 'node'; data: GraphNode }
      | { type: 'link'; data: GraphLink }
      | null;
    tab: 'graph' | 'table' | 'raw' | null;
  }>({ query: querySignature, item: null, tab: null });
  const currentSelection =
    selection.query === querySignature ? selection : null;
  const selectedItem = currentSelection ? currentSelection.item : null;
  const preferredTab = currentSelection ? currentSelection.tab : null;
  const setSelectedItem = (
    item:
      | { type: 'node'; data: GraphNode }
      | { type: 'link'; data: GraphLink }
      | null,
  ) =>
    setSelection((previous) => ({
      query: querySignature,
      item,
      tab: previous.query === querySignature ? previous.tab : null,
    }));
  const setPreferredTab = (tab: 'graph' | 'table' | 'raw') =>
    setSelection((previous) => ({
      query: querySignature,
      item: previous.query === querySignature ? previous.item : null,
      tab,
    }));

  // Below `md` the detail panel would leave the graph a sliver, so it starts
  // closed there and opens over the canvas rather than beside it.
  const narrow = useMediaQuery(theme.breakpoints.down('md'), { noSsr: true });
  const [detailOpen, setDetailOpen] = useState(defaultDetailOpen && !narrow);
  const [repulsion, setRepulsion] = useState(1);

  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);

  const nodeLabelKey = graphSettings?.node_label ?? 'label';
  const nodeColorByKey = graphSettings?.node_color_by ?? 'group';
  const edgeColor = theme.palette.text.secondary;

  // Extract graph data from query results, supporting both explicit-graph and path formats.
  const graphData = useMemo(
    () =>
      normalizeGraphData(
        records ? extractGraphData(records, nodeLabelKey) : null,
      ),
    [records, nodeLabelKey],
  );
  const severityGraph = useMemo(
    () =>
      !!graphData?.nodes.some((n) =>
        severityOf(readGraphValue(n, nodeColorByKey)),
      ),
    [graphData, nodeColorByKey],
  );

  // Determine which tabs are available based on the current results.
  const availableTabs = useMemo<('graph' | 'table' | 'raw')[]>(() => {
    if (!records || records.length === 0) return [];
    const tabs: ('graph' | 'table' | 'raw')[] = [];
    if (graphData && graphData.nodes.length > 0) tabs.push('graph');
    tabs.push('table');
    tabs.push('raw');
    return tabs;
  }, [records, graphData]);

  // Active tab: user preference if still valid, otherwise first available.
  const activeTab =
    preferredTab && availableTabs.includes(preferredTab)
      ? preferredTab
      : (availableTabs[0] ?? 'table');

  const runAdhocQueryRef = useRef(runAdhocQuery);
  runAdhocQueryRef.current = runAdhocQuery;
  const runHistoryQueryRef = useRef(runHistoryQuery);
  runHistoryQueryRef.current = runHistoryQuery;
  const needInputsRef = useRef(needInputs);
  needInputsRef.current = needInputs;

  useEffect(() => {
    if (queryHistoryId) {
      runHistoryQueryRef.current(queryHistoryId);
    } else if (!needInputsRef.current?.length) {
      runAdhocQueryRef.current(params, { force: (refreshKey ?? 0) > 0 });
    }
  }, [cypher, params, refreshKey, queryHistoryId]);

  useEffect(() => {
    if (tokenExpired) {
      onTokenExpired?.();
    }
  }, [tokenExpired, onTokenExpired]);

  // A fresh identity for every layout rebuild, which is what AutoFitEffect
  // watches. Derived from the same inputs as the rebuild below, so the fit and
  // the rebuild cannot drift apart, and no counter has to be bumped from
  // inside the effect.
  const layoutEpoch = useMemo(
    () => ({}),
    [graphData, nodeLabelKey, nodeColorByKey, edgeColor, repulsion],
  );

  // Rebuild XyFlow graph whenever extracted graph data or spread changes.
  useEffect(() => {
    if (!graphData || graphData.nodes.length === 0) {
      setNodes([]);
      setEdges([]);
      return;
    }
    const positions = computeLayout(
      graphData.nodes,
      graphData.links,
      800,
      450,
      repulsion,
    );
    setNodes(
      buildXyNodes(graphData.nodes, positions, nodeLabelKey, nodeColorByKey),
    );
    setEdges(buildXyEdges(graphData.links, edgeColor, positions));
  }, [
    graphData,
    nodeLabelKey,
    nodeColorByKey,
    edgeColor,
    setNodes,
    setEdges,
    repulsion,
  ]);

  const handleNodeClick = (_: React.MouseEvent, node: Node) => {
    const original =
      (node.data?.['original'] as GraphNode) ??
      (node.data as unknown as GraphNode);
    setSelectedItem({ type: 'node', data: original });
    setDetailOpen(true);
    const updated = applyFocusOpacity(nodes, edges, node.id, edgeColor);
    setNodes(updated.nodes);
    setEdges(updated.edges);
  };

  const handleEdgeClick = (_: React.MouseEvent, edge: Edge) => {
    const original = (edge.data?.['original'] as GraphLink) ?? {
      source: edge.source,
      target: edge.target,
    };
    setSelectedItem({ type: 'link', data: original as GraphLink });
    setDetailOpen(true);
    const updated = applyFocusOpacity(nodes, edges, edge.id, edgeColor);
    setNodes(updated.nodes);
    setEdges(updated.edges);
  };

  const handlePaneClick = () => {
    setSelectedItem(null);
    const updated = applyFocusOpacity(nodes, edges, null, edgeColor);
    setNodes(updated.nodes);
    setEdges(updated.edges);
  };

  // ── Error / loading states ────────────────────────────────────────────────

  if (cypher === undefined && !queryHistoryId) {
    return (
      <Card>
        {caption && (
          <Grid
            container
            spacing={0}
            sx={{ flexDirection: 'column', alignItems: 'center' }}
          >
            <CardHeader
              title={caption}
              slotProps={{ title: { component: 'h3' } }}
            />
          </Grid>
        )}
        <Grid
          container
          spacing={0}
          sx={{ flexDirection: 'column', alignItems: 'center' }}
        >
          <CardContent>
            <Error />
            <Typography variant="body2">Missing cypher query</Typography>
          </CardContent>
        </Grid>
      </Card>
    );
  }

  if (needInputs !== undefined && needInputs.length > 0) {
    return (
      <Card>
        {caption && (
          <Grid
            container
            spacing={0}
            sx={{ flexDirection: 'column', alignItems: 'center' }}
          >
            <CardHeader
              title={caption}
              slotProps={{ title: { component: 'h3' } }}
            />
          </Grid>
        )}
        <Grid
          container
          spacing={0}
          sx={{ flexDirection: 'column', alignItems: 'center' }}
        >
          <CardContent>
            <Typography variant="body2" align="center">
              (Set {needInputs.join(', ')})
            </Typography>
          </CardContent>
        </Grid>
      </Card>
    );
  }

  if (error) {
    return (
      <Typography variant="body2">
        Failed to load requested data, please reload.
      </Typography>
    );
  }

  if (queryErrors.length > 0) {
    return (
      <Card>
        {caption && (
          <Grid
            container
            sx={{ flexDirection: 'column', alignItems: 'center' }}
          >
            <CardHeader
              title={caption}
              slotProps={{ title: { component: 'h3' } }}
            />
          </Grid>
        )}
        <QueryValidationBadge errors={queryErrors} warnings={warnings} />
        <Grid
          container
          spacing={0}
          sx={{ flexDirection: 'column', alignItems: 'center' }}
        >
          <CardContent>
            <Typography variant="h4" component="p" align="center">
              N/A
            </Typography>
            <Typography variant="body2" align="center">
              Query failed
            </Typography>
          </CardContent>
        </Grid>
      </Card>
    );
  }

  if (loading || records === undefined) {
    return <GraphPanelSkeleton caption={caption} fillHeight={fillHeight} />;
  }

  if (records.length === 0) {
    return (
      <Card>
        {caption && (
          <Grid
            container
            spacing={0}
            sx={{ flexDirection: 'column', alignItems: 'center' }}
          >
            <CardHeader
              title={caption}
              slotProps={{ title: { component: 'h3' } }}
            />
          </Grid>
        )}
        <CardContent>
          <Box
            sx={{ display: 'flex', alignItems: 'flex-start', gap: 1, mb: 2 }}
          >
            <Error
              color="warning"
              fontSize="small"
              sx={{ mt: '2px', flexShrink: 0 }}
            />
            <Typography variant="body2">
              The query returned no results. If this is unexpected, check that
              the query returns graph data and that the MATCH clause finds data.
            </Typography>
          </Box>
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{ display: 'block' }}
            gutterBottom
          >
            Supported return formats
          </Typography>
          <Box
            sx={{
              bgcolor: 'action.hover',
              border: 1,
              borderColor: 'divider',
              borderRadius: 1,
              p: 1,
              fontFamily: fontFamilyMono,
              fontSize: 12,
              whiteSpace: 'pre',
            }}
          >
            {[
              '// Option 1: explicit graph map',
              'RETURN {',
              '  nodes: [{ id, label, group, ...props }],',
              '  links: [{ source: <node id>, target: <node id>, type }]',
              '}',
              '',
              '// Option 2: RETURN path',
              'MATCH path = (a)-[*]->(b)',
              'RETURN path',
            ].join('\n')}
          </Box>
        </CardContent>
      </Card>
    );
  }

  const canvasHeight = fillHeight ? '100%' : 450;
  const contentFlex = fillHeight
    ? {
        flex: 1,
        minHeight: 0,
        display: 'flex',
        flexDirection: 'column' as const,
      }
    : {};

  return (
    <Card
      sx={{
        // The header lays out against the panel's own width, not the screen's.
        containerType: 'inline-size',
        ...(fillHeight
          ? { height: '100%', display: 'flex', flexDirection: 'column' }
          : {}),
      }}
    >
      {/* Header row: tabs left, caption right. In a narrow panel the caption
          takes its own line above the tabs rather than running into them. */}
      <Box
        sx={{
          display: 'flex',
          alignItems: 'center',
          flexShrink: 0,
          flexWrap: 'wrap',
          pl: 2,
        }}
      >
        <Tabs
          value={activeTab}
          onChange={(_, v: 'graph' | 'table' | 'raw') => setPreferredTab(v)}
          textColor="primary"
          indicatorColor="primary"
          sx={{ minHeight: 48, '& .MuiTab-root': { minHeight: 48 } }}
        >
          {availableTabs.includes('graph') && (
            <Tab label="Graph" value="graph" />
          )}
          {availableTabs.includes('table') && (
            <Tab label="Table" value="table" />
          )}
          {availableTabs.includes('raw') && <Tab label="Raw" value="raw" />}
        </Tabs>
        {caption && (
          <Typography
            variant="subtitle1"
            component="h3"
            title={caption}
            sx={{
              color: 'text.secondary',
              minWidth: 0,
              ml: 'auto',
              overflow: 'hidden',
              pl: 2,
              pr: 2,
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
              '@container (max-width: 560px)': {
                order: -1,
                ml: 0,
                pl: 0,
                pt: 1.25,
                whiteSpace: 'normal',
                width: '100%',
              },
            }}
          >
            {caption}
          </Typography>
        )}
      </Box>
      <Divider sx={{ flexShrink: 0 }} />
      <QueryValidationBadge errors={queryErrors} warnings={warnings} />

      {/* Content area — flex-fills parent when fillHeight */}
      <Box sx={contentFlex}>
        {/* ── Table tab ─────────────────────────────────────────────── */}
        {activeTab === 'table' && (
          <CypherTable
            cypher={cypher}
            params={params}
            needInputs={needInputs}
            height={fillHeight ? '100%' : '400px'}
            reportQueryToken={reportQueryToken}
            preloadedRecords={records}
            embedded
          />
        )}

        {/* ── Raw tab ───────────────────────────────────────────────── */}
        {activeTab === 'raw' && (
          <Box
            sx={{
              ...(fillHeight ? { flex: 1, minHeight: 0 } : { height: 450 }),
              overflow: 'auto',
              p: 2,
              fontFamily: fontFamilyMono,
              fontSize: 12,
              bgcolor: 'action.hover',
              whiteSpace: 'pre',
            }}
          >
            {JSON.stringify(records, null, 2)}
          </Box>
        )}

        {/* ── Graph tab ─────────────────────────────────────────────── */}
        {activeTab === 'graph' && graphData && (
          <Box
            sx={{
              display: 'flex',
              // Narrow, the details sit below the graph instead of beside or
              // over it, so the node they describe stays in view.
              flexDirection: narrow ? 'column' : 'row',
              position: 'relative',
              ...(fillHeight ? { flex: 1, minHeight: 0 } : { height: 450 }),
            }}
          >
            {/* Graph canvas */}
            <Box
              sx={
                narrow
                  ? { flex: 1, minHeight: 0 }
                  : { flex: 1, height: canvasHeight }
              }
            >
              <ReactFlow
                nodes={nodes}
                edges={edges}
                onNodesChange={onNodesChange}
                onEdgesChange={onEdgesChange}
                onNodeClick={handleNodeClick}
                onEdgeClick={handleEdgeClick}
                onPaneClick={handlePaneClick}
                nodeTypes={nodeTypes}
                edgeTypes={edgeTypes}
                proOptions={{ hideAttribution: true }}
                fitView
                fitViewOptions={{ padding: 0.2 }}
                minZoom={0.1}
                maxZoom={4}
                style={{ background: theme.palette.background.paper }}
              >
                <AutoFitEffect trigger={layoutEpoch} />
                <GraphControls
                  repulsion={repulsion}
                  onRepulsionChange={setRepulsion}
                />
                <Background
                  variant={BackgroundVariant.Dots}
                  color={theme.palette.divider}
                  gap={20}
                  size={1}
                />
              </ReactFlow>
            </Box>

            {/* Toggle handle — always visible, attached to left edge of detail area */}
            <Box
              sx={{
                display: 'flex',
                alignItems: 'center',
                flexShrink: 0,
                ...(narrow
                  ? {
                      borderTop: 1,
                      justifyContent: 'flex-end',
                      px: 0.5,
                      py: 0.25,
                    }
                  : {
                      borderLeft: 1,
                      flexDirection: 'column',
                      justifyContent: 'flex-start',
                      pt: 1,
                      width: 28,
                    }),
                // After the border shorthand, which would reset it.
                borderColor: 'divider',
              }}
            >
              <Tooltip title="Details" placement="left">
                <IconButton
                  aria-expanded={detailOpen}
                  aria-label={detailOpen ? 'Hide details' : 'Show details'}
                  size="small"
                  onClick={() => setDetailOpen((v) => !v)}
                  sx={{ color: detailOpen ? 'primary.main' : 'text.secondary' }}
                >
                  {detailOpen ? (
                    <Info fontSize="small" />
                  ) : (
                    <InfoOutlined fontSize="small" />
                  )}
                </IconButton>
              </Tooltip>
            </Box>

            {/* Detail panel — summary when nothing selected, item details when selected */}
            {detailOpen && (
              <Box
                sx={{
                  flexShrink: 0,
                  overflow: 'auto',
                  p: 2,
                  ...(narrow
                    ? { borderTop: 1, height: '45%', pt: 1 }
                    : { borderLeft: 1, width: DETAIL_PANEL_WIDTH }),
                  borderColor: 'divider',
                }}
              >
                {selectedItem ? (
                  <GraphDetailPanel
                    type={selectedItem.type}
                    data={selectedItem.data}
                  />
                ) : (
                  <GraphSummaryPanel
                    nodes={graphData.nodes}
                    links={graphData.links}
                    nodeGroupKey={nodeColorByKey}
                    getColor={(g) =>
                      nodeColor(
                        g,
                        theme.palette.mode === 'dark'
                          ? chartPalette.dark
                          : chartPalette.light,
                        theme.palette.mode,
                        severityGraph,
                      )
                    }
                  />
                )}
              </Box>
            )}
          </Box>
        )}
      </Box>
    </Card>
  );
}

export default memo(CypherGraph);
