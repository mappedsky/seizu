import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
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
  type ReactFlowState,
  getStraightPath,
  useEdgesState,
  useNodesInitialized,
  useNodesState,
  useReactFlow,
  useStore,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import {
  useLazyCypherQuery,
  useLazyHistoryQuery,
} from 'src/hooks/useCypherQuery';
import QueryValidationBadge from 'src/components/reports/QueryValidationBadge';
import { GraphPanelSkeleton } from 'src/components/reports/PanelLoadingSkeletons';
import GraphDetailPanel from 'src/components/reports/GraphDetailPanel';
import { chartPalette } from 'src/theme/brand';
import {
  nonSeverityPalette,
  severityColor,
  severityOf,
} from 'src/theme/severity';
import { fontFamilyMono } from 'src/theme/typography';

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
  const SPRING_LEN = 110 * repulsion;
  const SPRING_K = 0.06;
  const REPULSION = 3000 * repulsion * repulsion;
  const MIN_NODE_DISTANCE = 72 * repulsion;
  const COLLISION_K = 0.18;
  const EDGE_AVOID_DISTANCE = 40;
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
            ? 1.8
            : 3;
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
  const zoom = useStore(zoomSelector);
  const [hovered, setHovered] = useState(false);
  const palette =
    theme.palette.mode === 'dark' ? chartPalette.dark : chartPalette.light;
  const color = nodeColor(
    data['group'],
    palette,
    theme.palette.mode,
    Boolean(data['severityGraph']),
  );
  const diameter = Number(data['diameter'] ?? DOT_MIN);
  const name = String(data['label'] ?? data['id'] ?? '');
  const group = String(data['group'] ?? '');
  const hoverId = graphNodeHoverId(data as unknown as GraphNode);
  const labelSide = (data['labelSide'] as LabelSide | null) ?? null;
  // A name that had no room at the overview comes back when there is room
  // (zoomed in) or when the node is the subject (hovered, focused, selected).
  const showName =
    labelSide !== null ||
    zoom >= LABEL_REVEAL_ZOOM ||
    hovered ||
    selected ||
    Boolean(data['focused']);
  const side = labelSide ?? 'right';
  const paper = theme.palette.background.paper;

  return (
    <Tooltip
      title={
        <>
          <Box component="div" sx={{ fontWeight: 600 }}>
            {name}
          </Box>
          <Box component="div">
            {group}
            {hoverId !== name ? ` · ${hoverId}` : ''}
          </Box>
        </>
      }
      arrow
      disableInteractive
      enterDelay={NODE_HOVER_ENTER_DELAY_MS}
      enterNextDelay={0}
      placement="top"
    >
      <div
        style={{ cursor: 'pointer', position: 'relative' }}
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
      >
        {HANDLE_SIDES.map((handleSide) => {
          const handle = HANDLE_DEFINITIONS[handleSide];
          return (
            <Handle
              key={`target-${handleSide}`}
              id={`target-${handleSide}`}
              type="target"
              position={handle.position}
              style={{ ...hiddenHandleStyle, ...handle.style }}
            />
          );
        })}
        {/* The star: its colour is its group, its size how connected it is. */}
        <div
          style={{
            width: diameter,
            height: diameter,
            borderRadius: '50%',
            background: color,
            border: `2px solid ${paper}`,
            boxSizing: 'border-box',
            boxShadow: selected
              ? `0 0 0 2px ${theme.palette.primary.main}`
              : 'none',
          }}
        />
        {showName ? (
          <span
            style={{
              color: theme.palette.text.primary,
              fontFamily: theme.typography.fontFamily ?? 'sans-serif',
              fontSize: NODE_LABEL_FONT_SIZE,
              fontWeight: selected || hovered ? 600 : 500,
              lineHeight: 1,
              pointerEvents: 'none',
              position: 'absolute',
              top: '50%',
              transform: 'translateY(-50%)',
              whiteSpace: 'nowrap',
              // A halo of the canvas colour, so the name reads over the
              // edges that pass behind it.
              textShadow: `0 0 2px ${paper}, 0 0 3px ${paper}, 0 0 4px ${paper}`,
              ...(side === 'right'
                ? { left: diameter + NODE_LABEL_GAP }
                : { right: diameter + NODE_LABEL_GAP }),
            }}
          >
            {shortNodeLabel(name)}
          </span>
        ) : null}
        {HANDLE_SIDES.map((handleSide) => {
          const handle = HANDLE_DEFINITIONS[handleSide];
          return (
            <Handle
              key={`source-${handleSide}`}
              id={`source-${handleSide}`}
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

// ─── Star sizing and names ───────────────────────────────────────────────────

/** A node's dot: the smallest a leaf can be and still be a target. */
const DOT_MIN = 14;
/** The largest a hub grows, so it never crowds the names around it. */
const DOT_MAX = 40;
const NODE_LABEL_FONT_SIZE = 12;
const NODE_LABEL_GAP = 6;
const NODE_LABEL_MAX_CHARS = 22;
/** Rough advance of one character of a node name, in px. */
const NODE_LABEL_CHAR_WIDTH = NODE_LABEL_FONT_SIZE * 0.56;
/** Zoom at which every name shows, whatever had room at the overview. */
const LABEL_REVEAL_ZOOM = 1.35;
/** Above this many relationships, their names wait for focus. */
const EDGE_LABEL_LIMIT = 24;
/** The closest an automatic fit zooms in. */
const FIT_MAX_ZOOM = 1.25;
/** The furthest a fit zooms out to keep every name in frame. */
const FIT_MIN_ZOOM = 0.8;
/** How strongly an edge draws at rest; focus brings it to full. */
const EDGE_REST_OPACITY = 0.5;
/** Room a relationship label leaves at each end of its edge, past the dots. */
const EDGE_LABEL_CLEARANCE = 4;
/** Length of the arrowhead at an edge's target, in px. */
const EDGE_ARROW_LENGTH = 8;
/** Most relationships a focused node labels on plates; a hub labels none. */
const FOCUS_LABEL_LIMIT = 12;
/**
 * Stroke patterns that tell relationship types apart, in the order types are
 * listed (most common first): colour already belongs to the node groups.
 */
const RELATIONSHIP_DASHES = [undefined, '6 3', '1.5 3', '8 3 1.5 3', '3 3'];

type LabelSide = 'right' | 'left';

const zoomSelector = (state: ReactFlowState) => state.transform[2];

/** A dot's diameter from its degree: hubs grow, by square root so a hub of
 *  forty is not ten times a node of four. */
export function dotDiameter(degree: number): number {
  return Math.round(Math.min(DOT_MAX, DOT_MIN + 4 * Math.sqrt(degree)));
}

function shortNodeLabel(name: string): string {
  return name.length > NODE_LABEL_MAX_CHARS
    ? `${name.slice(0, NODE_LABEL_MAX_CHARS - 1)}…`
    : name;
}

/**
 * Which side of each dot its name goes on, or null when neither side is clear
 * at the overview. Names are placed most-connected first, so a hub keeps its
 * name and a leaf in a crowd gives way; a name may not cover another name or
 * another dot.
 */
export function placeNodeLabels(
  nodes: Array<{ id: string; label: string; degree: number }>,
  centres: Map<string, { x: number; y: number }>,
  diameters: Map<string, number>,
): Map<string, { side: LabelSide | null; box?: LabelBox }> {
  const dots: Array<{ id: string; box: LabelBox }> = nodes.flatMap((n) => {
    const c = centres.get(n.id);
    const r = (diameters.get(n.id) ?? DOT_MIN) / 2;
    return c
      ? [
          {
            id: n.id,
            box: {
              left: c.x - r,
              right: c.x + r,
              top: c.y - r,
              bottom: c.y + r,
            },
          },
        ]
      : [];
  });
  const placed: LabelBox[] = [];
  const result = new Map<string, { side: LabelSide | null; box?: LabelBox }>();
  const order = [...nodes].sort(
    (a, b) => b.degree - a.degree || a.label.length - b.label.length,
  );
  for (const node of order) {
    const c = centres.get(node.id);
    if (!c) {
      result.set(node.id, { side: null });
      continue;
    }
    const r = (diameters.get(node.id) ?? DOT_MIN) / 2;
    const width = shortNodeLabel(node.label).length * NODE_LABEL_CHAR_WIDTH + 4;
    const half = NODE_LABEL_FONT_SIZE / 2 + 2;
    const candidates: Array<[LabelSide, LabelBox]> = [
      [
        'right',
        {
          left: c.x + r + NODE_LABEL_GAP,
          right: c.x + r + NODE_LABEL_GAP + width,
          top: c.y - half,
          bottom: c.y + half,
        },
      ],
      [
        'left',
        {
          left: c.x - r - NODE_LABEL_GAP - width,
          right: c.x - r - NODE_LABEL_GAP,
          top: c.y - half,
          bottom: c.y + half,
        },
      ],
    ];
    let chosen: { side: LabelSide | null; box?: LabelBox } = { side: null };
    for (const [side, box] of candidates) {
      const clear =
        placed.every((other) => boxOverlap(box, other) === 0) &&
        dots.every(
          (dot) => dot.id === node.id || boxOverlap(box, dot.box) === 0,
        );
      if (clear) {
        chosen = { side, box };
        placed.push(box);
        break;
      }
    }
    result.set(node.id, chosen);
  }
  return result;
}

// The type floor (fontSizeMin), in px: the offset below is arithmetic on it.
const RELATIONSHIP_LABEL_FONT_SIZE = 11;
const RELATIONSHIP_LABEL_GAP = 2;
const RELATIONSHIP_LABEL_OFFSET =
  RELATIONSHIP_LABEL_FONT_SIZE / 2 + RELATIONSHIP_LABEL_GAP;
const PLATE_OFFSET = RELATIONSHIP_LABEL_OFFSET + 8;
/**
 * Plates draw above the stars and names they may cross, except those in
 * focus: a lit star's name outranks any relationship label.
 */
const PLATE_Z = 1001;
const NAME_Z = PLATE_Z + 1;

export function relationshipLabelTransform(
  sourceX: number,
  sourceY: number,
  targetX: number,
  targetY: number,
  labelX: number,
  labelY: number,
  offset = RELATIONSHIP_LABEL_OFFSET,
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
  let offsetX = (dy / distance) * offset;
  let offsetY = (-dx / distance) * offset;
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
  const theme = useTheme();
  // A label with no clear place waits for its edge, or a node on it, to be
  // focused; it then sits on a plate, since it may cross a dot or a name.
  const showLabel =
    Boolean(label) &&
    !data?.labelMuted &&
    (!data?.labelHidden || data?.focused || selected);
  const plated = Boolean(data?.labelHidden);
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
  // At rest an edge draws faintly so the stars carry the picture; a focused
  // edge, or one in a focused neighbourhood, draws at full strength.
  const rest = data?.focused ? 1 : EDGE_REST_OPACITY;
  const labelOpacity =
    Number(
      (labelStyle as React.CSSProperties | undefined)?.opacity ??
        style?.opacity ??
        1,
    ) * (data?.focused ? 1 : 0.8);
  const edgeStyle = {
    ...style,
    opacity: Number(style?.opacity ?? 1) * rest,
  };

  return (
    <>
      <BaseEdge path={edgePath} markerEnd={markerEnd} style={edgeStyle} />
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
                // A plate stands further off its line, clear of the names
                // that sit level with their dots.
                plated ? PLATE_OFFSET : RELATIONSHIP_LABEL_OFFSET,
              ),
              transformOrigin: 'center',
              pointerEvents: 'none',
              // A plate may cross a dot or a name, so it draws above them.
              zIndex: plated ? PLATE_Z : undefined,
              color: String(labelColor),
              opacity: Number(labelOpacity),
              fontSize: RELATIONSHIP_LABEL_FONT_SIZE,
              fontWeight: 400,
              letterSpacing: '0.01em',
              lineHeight: 1,
              whiteSpace: 'nowrap',
              ...(plated
                ? {
                    background: theme.palette.background.paper,
                    border: `1px solid ${theme.palette.divider}`,
                    borderRadius: 3,
                    padding: '2px 4px',
                  }
                : {
                    textShadow: `0 0 3px ${theme.palette.background.paper}, 0 0 3px ${theme.palette.background.paper}`,
                  }),
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

/** How many relationships touch each node. */
function nodeDegrees(graphLinks: GraphLink[]): Map<string, number> {
  const degrees = new Map<string, number>();
  for (const l of graphLinks) {
    for (const end of [l.source, l.target]) {
      const id = String(typeof end === 'object' ? (end as GraphNode).id : end);
      degrees.set(id, (degrees.get(id) ?? 0) + 1);
    }
  }
  return degrees;
}

/** Each node's dot diameter, from its degree. */
export function nodeDiameters(
  graphNodes: GraphNode[],
  graphLinks: GraphLink[],
): Map<string, number> {
  const degrees = nodeDegrees(graphLinks);
  return new Map(
    graphNodes.map((n) => [
      String(n.id),
      dotDiameter(degrees.get(String(n.id)) ?? 0),
    ]),
  );
}

/** Where the names drawn at the overview sit, for edge labels to avoid. */
export function nodeLabelBoxes(
  graphNodes: GraphNode[],
  graphLinks: GraphLink[],
  centres: Map<string, { x: number; y: number }>,
  nodeLabelKey: string,
): LabelBox[] {
  const degrees = nodeDegrees(graphLinks);
  const sides = placeNodeLabels(
    graphNodes.map((n) => ({
      id: String(n.id),
      label: String(readGraphValue(n, nodeLabelKey) ?? n.id ?? ''),
      degree: degrees.get(String(n.id)) ?? 0,
    })),
    centres,
    nodeDiameters(graphNodes, graphLinks),
  );
  return [...sides.values()].flatMap((placed) =>
    placed.box ? [placed.box] : [],
  );
}

function buildXyNodes(
  graphNodes: GraphNode[],
  graphLinks: GraphLink[],
  centres: Map<string, { x: number; y: number }>,
  nodeLabelKey: string,
  nodeColorByKey: string,
): Node[] {
  const severityGraph = graphNodes.some((n) =>
    severityOf(readGraphValue(n, nodeColorByKey)),
  );
  const degrees = nodeDegrees(graphLinks);
  const diameters = nodeDiameters(graphNodes, graphLinks);
  const labelOf = (n: GraphNode) =>
    String(readGraphValue(n, nodeLabelKey) ?? n.id ?? '');
  const sides = placeNodeLabels(
    graphNodes.map((n) => ({
      id: String(n.id),
      label: labelOf(n),
      degree: degrees.get(String(n.id)) ?? 0,
    })),
    centres,
    diameters,
  );
  return graphNodes.map((n) => {
    const id = String(n.id);
    const centre = centres.get(id) ?? { x: 0, y: 0 };
    const diameter = diameters.get(id) ?? DOT_MIN;
    return {
      id,
      type: 'graphNode',
      // Layout positions are centres; React Flow places a node by its corner.
      position: { x: centre.x - diameter / 2, y: centre.y - diameter / 2 },
      data: {
        ...n,
        label: labelOf(n),
        group: String(readGraphValue(n, nodeColorByKey) ?? 'default'),
        severityGraph,
        diameter,
        labelSide: sides.get(id)?.side ?? null,
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
  centres: Map<string, { x: number; y: number }>,
  diameters: Map<string, number> = new Map(),
  obstacles: LabelBox[] = [],
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

  const centre = (id: string) => centres.get(id);
  const nodeBoxes: LabelBox[] = [
    ...[...centres.entries()].map(([id, c]) => {
      const r = (diameters.get(id) ?? DOT_MIN) / 2;
      return { left: c.x - r, right: c.x + r, top: c.y - r, bottom: c.y + r };
    }),
  ];

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

  // The node names already drawn: a relationship label may not cover one.
  const placed: LabelBox[] = [...obstacles];
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

    // A label sits on the stretch of line between the dots, clear of the
    // arrowhead; a label longer than that stretch waits for focus.
    const start = (diameters.get(endpoints[i][0]) ?? DOT_MIN) / 2;
    const end =
      length -
      (diameters.get(endpoints[i][1]) ?? DOT_MIN) / 2 -
      EDGE_ARROW_LENGTH;
    const fits = (t: number) =>
      t * length - width / 2 >= start + EDGE_LABEL_CLEARANCE &&
      t * length + width / 2 <= end - EDGE_LABEL_CLEARANCE;

    // The best position on the clear stretch, and the least crowded one
    // anywhere: a held-back label takes the latter when focus shows it.
    type Spot = { t: number; labels: number; nodes: number; box: LabelBox };
    const better = (a: Spot, b: Spot | undefined) =>
      !b || a.labels < b.labels || (a.labels === b.labels && a.nodes < b.nodes);
    let best: Spot | undefined;
    let fallback: Spot | undefined;
    for (const t of LABEL_POSITIONS) {
      const x = from.x + dx * t;
      const y = from.y + dy * t;
      const box = {
        left: x - halfW,
        right: x + halfW,
        top: y - halfH,
        bottom: y + halfH,
      };
      const spot = {
        t,
        box,
        labels: placed.reduce((sum, o) => sum + boxOverlap(box, o), 0),
        nodes: nodeBoxes.reduce((sum, n) => sum + boxOverlap(box, n), 0),
      };
      if (better(spot, fallback)) fallback = spot;
      if (fits(t) && better(spot, best)) best = spot;
      if (best && best.labels === 0 && best.nodes === 0) break;
    }
    if (best && best.labels === 0) {
      results[i].labelT = best.t;
      placed.push(best.box);
    } else {
      results[i].labelT = (best ?? fallback)?.t ?? 0.5;
      results[i].labelHidden = true;
    }
  }
  return results;
}

/**
 * The relationship types in a graph, most common first (ties by name), with
 * how many relationships of each there are and the stroke that draws them.
 */
export function relationshipTypes(
  graphLinks: GraphLink[],
): Array<{ type: string; count: number; dash: string | undefined }> {
  const counts = new Map<string, number>();
  for (const l of graphLinks) {
    if (l.type) counts.set(l.type, (counts.get(l.type) ?? 0) + 1);
  }
  const single = counts.size < 2;
  return [...counts]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([type, count], i) => ({
      type,
      count,
      dash: single
        ? undefined
        : RELATIONSHIP_DASHES[i % RELATIONSHIP_DASHES.length],
    }));
}

export function buildXyEdges(
  graphLinks: GraphLink[],
  edgeColor: string,
  positions?: Map<string, { x: number; y: number }>,
  diameters?: Map<string, number>,
  obstacles?: LabelBox[],
): Edge[] {
  // A dense graph keeps its relationship names for focus; drawn all at once
  // they are a texture, not information.
  const quietLabels = graphLinks.length > EDGE_LABEL_LIMIT;
  const labels = positions
    ? placeEdgeLabels(graphLinks, positions, diameters, obstacles).map(
        (placed) => ({
          ...placed,
          labelHidden: placed.labelHidden || quietLabels,
        }),
      )
    : graphLinks.map((l) => ({
        label: l.type ?? undefined,
        labelT: 0.5,
        labelHidden: false,
      }));
  const dashes = new Map(
    relationshipTypes(graphLinks).map(({ type, dash }) => [type, dash]),
  );
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
      style: {
        stroke: edgeColor,
        strokeWidth: 1.2,
        opacity: FULL_OPACITY,
        ...(l.type && dashes.get(l.type)
          ? { strokeDasharray: dashes.get(l.type) }
          : {}),
      },
      markerEnd: { type: MarkerType.ArrowClosed, color: edgeColor },
      data: {
        original: l,
        relType: l.type ?? null,
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
        zIndex: 0,
        data: { ...n.data, focused: false },
        style: { ...n.style, opacity: FULL_OPACITY, transition },
      })),
      edges: edges.map((e) => ({
        ...e,
        data: { ...e.data, labelMuted: false, focused: false },
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
    // A hub's relationships would stack their labels over each other; it
    // keeps only those already drawn, and the legend names the rest.
    const quietFocus = connectedEdgeIds.size > FOCUS_LABEL_LIMIT;
    return {
      nodes: nodes.map((n) => ({
        ...n,
        zIndex: neighborNodeIds.has(n.id) ? NAME_Z : 0,
        data: { ...n.data, focused: neighborNodeIds.has(n.id) },
        style: {
          ...n.style,
          opacity: neighborNodeIds.has(n.id) ? FULL_OPACITY : DIM_OPACITY,
          transition,
        },
      })),
      edges: edges.map((e) => ({
        ...e,
        data: {
          ...e.data,
          labelMuted: quietFocus && Boolean(e.data?.['labelHidden']),
          focused: connectedEdgeIds.has(e.id),
        },
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
      zIndex: litNodeIds.has(n.id) ? NAME_Z : 0,
      data: { ...n.data, focused: litNodeIds.has(n.id) },
      style: {
        ...n.style,
        opacity: litNodeIds.has(n.id) ? FULL_OPACITY : DIM_OPACITY,
        transition,
      },
    })),
    edges: edges.map((e) => ({
      ...e,
      data: { ...e.data, labelMuted: false, focused: e.id === focusedId },
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

/**
 * Light one group: its nodes and the relationships between them stay at full
 * strength and show their names; everything else dims. A null group clears.
 */
export function applyGroupFocus(
  nodes: Node[],
  edges: Edge[],
  group: string | null,
  edgeColor: string,
): { nodes: Node[]; edges: Edge[] } {
  if (group === null) return applyFocusOpacity(nodes, edges, null, edgeColor);
  const transition = 'opacity 0.2s';
  const inGroup = new Set(
    nodes.filter((n) => n.data?.['group'] === group).map((n) => n.id),
  );
  const lit = (e: Edge) => inGroup.has(e.source) && inGroup.has(e.target);
  return {
    nodes: nodes.map((n) => ({
      ...n,
      zIndex: inGroup.has(n.id) ? NAME_Z : 0,
      data: { ...n.data, focused: inGroup.has(n.id) },
      style: {
        ...n.style,
        opacity: inGroup.has(n.id) ? FULL_OPACITY : DIM_OPACITY,
        transition,
      },
    })),
    edges: edges.map((e) => ({
      ...e,
      data: { ...e.data, labelMuted: false, focused: lit(e) },
      style: {
        ...e.style,
        stroke: edgeColor,
        opacity: lit(e) ? FULL_OPACITY : DIM_OPACITY,
        transition,
      },
      labelStyle: {
        ...e.labelStyle,
        opacity: lit(e) ? FULL_OPACITY : DIM_OPACITY,
      },
    })),
  };
}

/**
 * Light one relationship type: its relationships and the nodes they join stay
 * at full strength, and the nodes show their names; everything else dims. The
 * relationships keep their labels quiet, since the legend already names them.
 */
export function applyTypeFocus(
  nodes: Node[],
  edges: Edge[],
  type: string | null,
  edgeColor: string,
): { nodes: Node[]; edges: Edge[] } {
  if (type === null) return applyFocusOpacity(nodes, edges, null, edgeColor);
  const transition = 'opacity 0.2s';
  const lit = (e: Edge) => e.data?.['relType'] === type;
  const ends = new Set(edges.filter(lit).flatMap((e) => [e.source, e.target]));
  return {
    nodes: nodes.map((n) => ({
      ...n,
      zIndex: ends.has(n.id) ? NAME_Z : 0,
      data: { ...n.data, focused: ends.has(n.id) },
      style: {
        ...n.style,
        opacity: ends.has(n.id) ? FULL_OPACITY : DIM_OPACITY,
        transition,
      },
    })),
    edges: edges.map((e) => ({
      ...e,
      data: { ...e.data, labelMuted: true, focused: lit(e) },
      style: {
        ...e.style,
        stroke: edgeColor,
        opacity: lit(e) ? FULL_OPACITY : DIM_OPACITY,
        transition,
      },
      labelStyle: {
        ...e.labelStyle,
        opacity: lit(e) ? FULL_OPACITY : DIM_OPACITY,
      },
    })),
  };
}

// ─── Legend ──────────────────────────────────────────────────────────────────

interface LegendGroup {
  group: string;
  count: number;
  color: string;
}

/** What the legend has lit: a node group or a relationship type. */
export type LegendFocus = { kind: 'group' | 'type'; value: string };

/** How many entries a legend row lists before folding the rest into a count. */
const LEGEND_MAX_ENTRIES = 8;

const legendChipSx = {
  alignItems: 'center',
  bgcolor: 'transparent',
  border: 1,
  borderColor: 'transparent',
  borderRadius: 999,
  color: 'text.primary',
  display: 'inline-flex',
  font: 'inherit',
  fontSize: '0.75rem',
  gap: 0.75,
  lineHeight: 1,
  px: 0.875,
  py: 0.5,
} as const;

function LegendChip({
  swatch,
  name,
  count,
  active,
  onToggle,
}: {
  swatch: React.ReactNode;
  name: string;
  count: number;
  active: boolean;
  /** Absent when the row has one entry: lighting it would light everything. */
  onToggle?: () => void;
}) {
  const content = (
    <>
      {swatch}
      <span>{name}</span>
      <Box
        component="span"
        sx={{ color: 'text.secondary', fontVariantNumeric: 'tabular-nums' }}
      >
        {count}
      </Box>
    </>
  );
  if (!onToggle) {
    return (
      <Box component="span" sx={legendChipSx}>
        {content}
      </Box>
    );
  }
  return (
    <Box
      component="button"
      type="button"
      aria-pressed={active}
      onClick={onToggle}
      sx={{
        ...legendChipSx,
        cursor: 'pointer',
        ...(active && {
          bgcolor: 'action.selected',
          borderColor: 'primary.main',
        }),
        '&:hover': { bgcolor: active ? 'action.selected' : 'action.hover' },
        '&:focus-visible': { outline: 2, outlineColor: 'primary.main' },
      }}
    >
      {content}
    </Box>
  );
}

function LegendRow({
  title,
  label,
  children,
  folded,
}: {
  title: string;
  label: string;
  children: React.ReactNode;
  folded: number;
}) {
  return (
    <>
      <Typography
        component="p"
        variant="caption"
        sx={{ color: 'text.secondary', py: 0.5, whiteSpace: 'nowrap' }}
      >
        {title}
      </Typography>
      <Box
        role="group"
        aria-label={label}
        sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5, minWidth: 0 }}
      >
        {children}
        {folded > 0 ? (
          <Typography
            variant="caption"
            sx={{ alignSelf: 'center', color: 'text.secondary', px: 0.5 }}
          >
            +{folded} more
          </Typography>
        ) : null}
      </Box>
    </>
  );
}

/**
 * The key to the chart, in a strip above it so it never covers a star: one
 * row for the node groups (colour) and one for the relationship types (stroke),
 * each entry with its count. An entry is a button that lights it.
 */
function GraphLegend({
  groups,
  types,
  nodeCount,
  linkCount,
  edgeColor,
  active,
  onToggle,
}: {
  groups: LegendGroup[];
  types: Array<{ type: string; count: number; dash: string | undefined }>;
  nodeCount: number;
  linkCount: number;
  edgeColor: string;
  active: LegendFocus | null;
  onToggle: (focus: LegendFocus) => void;
}) {
  const shownGroups = groups.slice(0, LEGEND_MAX_ENTRIES);
  const shownTypes = types.slice(0, LEGEND_MAX_ENTRIES);
  const isActive = (kind: LegendFocus['kind'], value: string) =>
    active?.kind === kind && active.value === value;
  return (
    <Box
      sx={{
        alignItems: 'start',
        borderBottom: 1,
        borderColor: 'divider',
        columnGap: 1.5,
        display: 'grid',
        flexShrink: 0,
        gridTemplateColumns: 'auto minmax(0, 1fr)',
        px: 2,
        py: 0.5,
        rowGap: 0.25,
      }}
    >
      <LegendRow
        title={`${nodeCount} ${nodeCount === 1 ? 'node' : 'nodes'}`}
        label="Node groups"
        folded={groups.length - shownGroups.length}
      >
        {shownGroups.map(({ group, count, color }) => (
          <LegendChip
            key={group}
            name={group}
            count={count}
            active={isActive('group', group)}
            onToggle={
              groups.length > 1
                ? () => onToggle({ kind: 'group', value: group })
                : undefined
            }
            swatch={
              <Box
                component="span"
                aria-hidden
                sx={{
                  bgcolor: color,
                  borderRadius: '50%',
                  flexShrink: 0,
                  height: 8,
                  width: 8,
                }}
              />
            }
          />
        ))}
      </LegendRow>
      {linkCount > 0 ? (
        <LegendRow
          title={`${linkCount} ${linkCount === 1 ? 'relationship' : 'relationships'}`}
          label="Relationship types"
          folded={types.length - shownTypes.length}
        >
          {shownTypes.map(({ type, count, dash }) => (
            <LegendChip
              key={type}
              name={type}
              count={count}
              active={isActive('type', type)}
              onToggle={
                types.length > 1
                  ? () => onToggle({ kind: 'type', value: type })
                  : undefined
              }
              swatch={
                <Box
                  component="svg"
                  aria-hidden
                  viewBox="0 0 18 8"
                  sx={{ flexShrink: 0, height: 8, width: 18 }}
                >
                  <line
                    x1="0"
                    y1="4"
                    x2="13"
                    y2="4"
                    stroke={edgeColor}
                    strokeWidth="1.5"
                    strokeDasharray={dash}
                  />
                  <path d="M12 1 L18 4 L12 7 Z" fill={edgeColor} />
                </Box>
              }
            />
          ))}
        </LegendRow>
      ) : null}
    </Box>
  );
}

// ─── Auto-fit helper ─────────────────────────────────────────────────────────
// Rendered inside <ReactFlow> so useReactFlow() is in context.

/**
 * Fits the view with a smooth transition whenever `trigger` changes identity —
 * every layout rebuild — skipping the initial mount.
 */
/**
 * Fits the chart to its stars *and* the names beside them, which React Flow's
 * own fit does not see, so a name at the edge is not cut off. A small graph
 * stops at FIT_MAX_ZOOM rather than filling the panel with outsized type.
 */
function useFitWithNames() {
  const { getNodes, setViewport } = useReactFlow();
  const width = useStore((state) => state.width);
  const height = useStore((state) => state.height);
  return useCallback(
    (duration = 0) => {
      const placed = getNodes();
      if (placed.length === 0 || width === 0 || height === 0) return;
      let left = Infinity;
      let right = -Infinity;
      let dotsLeft = Infinity;
      let dotsRight = -Infinity;
      let top = Infinity;
      let bottom = -Infinity;
      for (const node of placed) {
        const d = Number(node.data?.['diameter'] ?? DOT_MIN);
        const name = shortNodeLabel(String(node.data?.['label'] ?? ''));
        const nameWidth = name.length * NODE_LABEL_CHAR_WIDTH;
        const side = node.data?.['labelSide'] as LabelSide | null;
        const x = node.position.x;
        const y = node.position.y;
        left = Math.min(
          left,
          side === 'left' ? x - NODE_LABEL_GAP - nameWidth : x,
        );
        right = Math.max(
          right,
          side === 'right' ? x + d + NODE_LABEL_GAP + nameWidth : x + d,
        );
        dotsLeft = Math.min(dotsLeft, x);
        dotsRight = Math.max(dotsRight, x + d);
        top = Math.min(top, y);
        bottom = Math.max(bottom, y + d);
      }
      const margin = 24;
      // The zoom and spread controls sit over the left edge; the chart is
      // framed to their right so no star lands under them.
      const controlsGutter = 48;
      const usableWidth = width - 2 * margin - controlsGutter;
      const zoomFor = (w: number, h: number) =>
        Math.min(
          FIT_MAX_ZOOM,
          usableWidth / Math.max(w, 1),
          (height - 2 * margin) / Math.max(h, 1),
        );
      // Fitting every name can shrink a wide graph past reading; below
      // FIT_MIN_ZOOM, the stars are fitted and an outer name may run off,
      // to be read by panning or hovering.
      let zoom = zoomFor(right - left, bottom - top);
      if (zoom < FIT_MIN_ZOOM) {
        zoom = Math.max(
          zoom,
          Math.min(FIT_MIN_ZOOM, zoomFor(dotsRight - dotsLeft, bottom - top)),
        );
        left = dotsLeft;
        right = dotsRight;
      }
      setViewport(
        {
          x:
            controlsGutter +
            (width - controlsGutter) / 2 -
            ((left + right) / 2) * zoom,
          y: height / 2 - ((top + bottom) / 2) * zoom,
          zoom,
        },
        { duration },
      );
    },
    [getNodes, setViewport, width, height],
  );
}

function AutoFitEffect({ trigger }: { trigger: unknown }) {
  const fit = useFitWithNames();
  const initialized = useNodesInitialized();
  useEffect(() => {
    if (!initialized) return undefined;
    // After the frame that places the rebuilt nodes.
    const id = requestAnimationFrame(() => fit(200));
    return () => cancelAnimationFrame(id);
  }, [trigger, fit, initialized]);
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
  const { zoomIn, zoomOut } = useReactFlow();
  const fit = useFitWithNames();

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
        <CtrlBtn title="Fit view" onClick={() => fit(200)}>
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

  // Below `md` the detail panel would leave the graph a sliver, so it opens
  // below the canvas rather than beside it. It starts closed and opens on a
  // selection.
  const narrow = useMediaQuery(theme.breakpoints.down('md'), { noSsr: true });
  const [detailOpen, setDetailOpen] = useState(false);
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
      buildXyNodes(
        graphData.nodes,
        graphData.links,
        positions,
        nodeLabelKey,
        nodeColorByKey,
      ),
    );
    setEdges(
      buildXyEdges(
        graphData.links,
        edgeColor,
        positions,
        nodeDiameters(graphData.nodes, graphData.links),
        nodeLabelBoxes(
          graphData.nodes,
          graphData.links,
          positions,
          nodeLabelKey,
        ),
      ),
    );
  }, [
    graphData,
    nodeLabelKey,
    nodeColorByKey,
    edgeColor,
    setNodes,
    setEdges,
    repulsion,
  ]);

  // The group or relationship type lit from the legend, for the query it
  // was lit on.
  const [legendFocus, setLegendFocus] = useState<{
    query: string;
    focus: LegendFocus;
  } | null>(null);
  const activeFocus =
    legendFocus && legendFocus.query === querySignature
      ? legendFocus.focus
      : null;
  const legendTypes = useMemo(
    () => (graphData ? relationshipTypes(graphData.links) : []),
    [graphData],
  );
  const legendGroups = useMemo<LegendGroup[]>(() => {
    if (!graphData) return [];
    const palette =
      theme.palette.mode === 'dark' ? chartPalette.dark : chartPalette.light;
    const counts = new Map<string, number>();
    for (const n of graphData.nodes) {
      const group = String(readGraphValue(n, nodeColorByKey) ?? 'default');
      counts.set(group, (counts.get(group) ?? 0) + 1);
    }
    return [...counts]
      .sort((a, b) => b[1] - a[1])
      .map(([group, count]) => ({
        group,
        count,
        color: nodeColor(group, palette, theme.palette.mode, severityGraph),
      }));
  }, [graphData, nodeColorByKey, severityGraph, theme.palette.mode]);

  const handleToggleLegend = (focus: LegendFocus) => {
    const same =
      activeFocus?.kind === focus.kind && activeFocus.value === focus.value;
    setLegendFocus(same ? null : { query: querySignature, focus });
    setSelectedItem(null);
    const apply = focus.kind === 'group' ? applyGroupFocus : applyTypeFocus;
    const updated = apply(nodes, edges, same ? null : focus.value, edgeColor);
    setNodes(updated.nodes);
    setEdges(updated.edges);
  };

  // Hovering a star previews its neighbourhood, unless a selection or a lit
  // legend entry already holds the view.
  const previewing = selectedItem === null && activeFocus === null;
  const handleNodeMouseEnter = (_: React.MouseEvent, node: Node) => {
    if (!previewing) return;
    const updated = applyFocusOpacity(nodes, edges, node.id, edgeColor);
    setNodes(updated.nodes);
    setEdges(updated.edges);
  };
  const handleNodeMouseLeave = () => {
    if (!previewing) return;
    const updated = applyFocusOpacity(nodes, edges, null, edgeColor);
    setNodes(updated.nodes);
    setEdges(updated.edges);
  };

  const handleNodeClick = (_: React.MouseEvent, node: Node) => {
    setLegendFocus(null);
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
    setLegendFocus(null);
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
    setLegendFocus(null);
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
          <GraphLegend
            groups={legendGroups}
            types={legendTypes}
            nodeCount={graphData.nodes.length}
            linkCount={graphData.links.length}
            edgeColor={edgeColor}
            active={activeFocus}
            onToggle={handleToggleLegend}
          />
        )}
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
                onNodeMouseEnter={handleNodeMouseEnter}
                onNodeMouseLeave={handleNodeMouseLeave}
                onEdgeClick={handleEdgeClick}
                onPaneClick={handlePaneClick}
                nodeTypes={nodeTypes}
                edgeTypes={edgeTypes}
                proOptions={{ hideAttribution: true }}
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

            {/* Detail panel — the selected item's properties */}
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
                  <Typography variant="body2" color="text.secondary">
                    Select a node or relationship to see its properties.
                  </Typography>
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
