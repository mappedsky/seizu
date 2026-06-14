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
  const color = colorForGroup(String(data['group'] ?? 'default'), palette);
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
            width: 64,
            height: 64,
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
            fontSize: 9,
            fontWeight: 600,
            lineHeight: 1.1,
            overflow: 'hidden',
            padding: 8,
            textTransform: 'none',
          }}
        >
          <span
            style={{
              display: '-webkit-box',
              WebkitBoxOrient: 'vertical',
              WebkitLineClamp: 4,
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

const RELATIONSHIP_LABEL_FONT_SIZE = 9;
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
  } = props;
  const [edgePath, labelX, labelY] = getStraightPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
  });
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
      {label ? (
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
        original: n,
      },
    };
  });
}

export function buildXyEdges(
  graphLinks: GraphLink[],
  edgeColor: string,
  positions?: Map<string, { x: number; y: number }>,
): Edge[] {
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
      label: l.type ?? undefined,
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
      data: { original: l },
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
// Rendered inside <ReactFlow> so useReactFlow() is in context. Calls fitView
// with a smooth transition whenever `trigger` increments (skipping initial mount).

function AutoFitEffect({ trigger }: { trigger: number }) {
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

  const [selectedItem, setSelectedItem] = useState<
    { type: 'node'; data: GraphNode } | { type: 'link'; data: GraphLink } | null
  >(null);

  const [detailOpen, setDetailOpen] = useState(defaultDetailOpen);
  const [preferredTab, setPreferredTab] = useState<
    'graph' | 'table' | 'raw' | null
  >(null);
  const [repulsion, setRepulsion] = useState(1);
  const [_focusedId, setFocusedId] = useState<string | null>(null);
  const [fitViewTrigger, setFitViewTrigger] = useState(0);

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

  // Clear selection and tab preference when the query changes.
  useEffect(() => {
    setSelectedItem(null);
    setFocusedId(null);
    setPreferredTab(null);
  }, [cypher, queryHistoryId]);

  // Rebuild XyFlow graph whenever extracted graph data or spread changes.
  useEffect(() => {
    if (!graphData || graphData.nodes.length === 0) {
      setNodes([]);
      setEdges([]);
      setFocusedId(null);
      return;
    }
    setFocusedId(null);
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
    setFitViewTrigger((n) => n + 1);
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
    setFocusedId(node.id);
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
    setFocusedId(edge.id);
    const updated = applyFocusOpacity(nodes, edges, edge.id, edgeColor);
    setNodes(updated.nodes);
    setEdges(updated.edges);
  };

  const handlePaneClick = () => {
    setSelectedItem(null);
    setFocusedId(null);
    const updated = applyFocusOpacity(nodes, edges, null, edgeColor);
    setNodes(updated.nodes);
    setEdges(updated.edges);
  };

  // ── Error / loading states ────────────────────────────────────────────────

  if (cypher === undefined && !queryHistoryId) {
    return (
      <Card>
        {caption && (
          <>
            <Grid
              container
              spacing={0}
              sx={{ flexDirection: 'column', alignItems: 'center' }}
            >
              <CardHeader title={caption} />
            </Grid>
            <Divider />
          </>
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
          <>
            <Grid
              container
              spacing={0}
              sx={{ flexDirection: 'column', alignItems: 'center' }}
            >
              <CardHeader title={caption} />
            </Grid>
            <Divider />
          </>
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
          <>
            <Grid
              container
              sx={{ flexDirection: 'column', alignItems: 'center' }}
            >
              <CardHeader title={caption} />
            </Grid>
            <Divider />
          </>
        )}
        <QueryValidationBadge errors={queryErrors} warnings={warnings} />
        <Grid
          container
          spacing={0}
          sx={{ flexDirection: 'column', alignItems: 'center' }}
        >
          <CardContent>
            <Typography variant="h4" align="center">
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
          <>
            <Grid
              container
              spacing={0}
              sx={{ flexDirection: 'column', alignItems: 'center' }}
            >
              <CardHeader title={caption} />
            </Grid>
            <Divider />
          </>
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
              fontFamily: 'monospace',
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
      sx={
        fillHeight
          ? { height: '100%', display: 'flex', flexDirection: 'column' }
          : {}
      }
    >
      {/* Header row: tabs left, optional spread control, caption right */}
      <Box sx={{ display: 'flex', alignItems: 'center', pl: 2, flexShrink: 0 }}>
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
            sx={{ ml: 'auto', pr: 2, color: 'text.secondary' }}
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
          />
        )}

        {/* ── Raw tab ───────────────────────────────────────────────── */}
        {activeTab === 'raw' && (
          <Box
            sx={{
              ...(fillHeight ? { flex: 1, minHeight: 0 } : { height: 450 }),
              overflow: 'auto',
              p: 2,
              fontFamily: 'monospace',
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
              ...(fillHeight ? { flex: 1, minHeight: 0 } : { height: 450 }),
            }}
          >
            {/* Graph canvas */}
            <Box sx={{ flex: 1, height: canvasHeight }}>
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
                <AutoFitEffect trigger={fitViewTrigger} />
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
                flexDirection: 'column',
                alignItems: 'center',
                justifyContent: 'flex-start',
                pt: 1,
                borderLeft: 1,
                borderColor: 'divider',
                width: 28,
                flexShrink: 0,
              }}
            >
              <Tooltip title="Details" placement="left">
                <IconButton
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
                  width: DETAIL_PANEL_WIDTH,
                  flexShrink: 0,
                  borderLeft: 1,
                  borderColor: 'divider',
                  overflow: 'auto',
                  p: 2,
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
                      colorForGroup(
                        g,
                        theme.palette.mode === 'dark'
                          ? chartPalette.dark
                          : chartPalette.light,
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
