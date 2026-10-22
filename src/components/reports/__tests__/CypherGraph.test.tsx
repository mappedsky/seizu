import { type Edge, type Node } from '@xyflow/react';
import {
  applyFocusOpacity,
  buildXyEdges,
  placeEdgeLabels,
  closestEdgeHandles,
  computeLayout,
  extractGraphData,
  graphNodeHoverId,
  pointToSegmentDistance,
  relationshipLabelTransform,
} from '../CypherGraph';

const EDGE_COLOR = '#aaaaaa';

function makeNode(id: string, style?: React.CSSProperties): Node {
  return {
    id,
    position: { x: 0, y: 0 },
    data: {},
    style,
  };
}

function makeEdge(
  id: string,
  source: string,
  target: string,
  label?: string,
): Edge {
  return {
    id,
    source,
    target,
    label,
    style: { stroke: EDGE_COLOR, strokeWidth: 1.5 },
    labelStyle: { fontSize: 10 },
    labelBgStyle: {},
    data: {},
  };
}

const NODES = [makeNode('a'), makeNode('b'), makeNode('c'), makeNode('d')];

// a → b, a → c, d (isolated)
const EDGES = [
  makeEdge('edge-0', 'a', 'b', 'REL_AB'),
  makeEdge('edge-1', 'a', 'c', 'REL_AC'),
  makeEdge('edge-2', 'c', 'd', 'REL_CD'),
];

function distance(
  positions: Map<string, { x: number; y: number }>,
  a: string,
  b: string,
): number {
  const pa = positions.get(a)!;
  const pb = positions.get(b)!;
  return Math.sqrt((pb.x - pa.x) ** 2 + (pb.y - pa.y) ** 2);
}

describe('computeLayout', () => {
  it('returns an empty layout for malformed graph data', () => {
    expect(computeLayout({ length: 1 }, { length: 1 }, 800, 450).size).toBe(0);
  });

  it('spreads disconnected components farther apart as repulsion increases', () => {
    const graphNodes = [{ id: 'a' }, { id: 'b' }, { id: 'c' }, { id: 'd' }];
    const graphLinks = [
      { source: 'a', target: 'b', type: 'AB' },
      { source: 'c', target: 'd', type: 'CD' },
    ];

    const compact = computeLayout(graphNodes, graphLinks, 800, 450, 0.5);
    const spread = computeLayout(graphNodes, graphLinks, 800, 450, 2);

    expect(distance(spread, 'a', 'c')).toBeGreaterThan(
      distance(compact, 'a', 'c'),
    );
  });
});

describe('pointToSegmentDistance', () => {
  it('measures distance to the interior of a relationship segment', () => {
    expect(
      pointToSegmentDistance(
        { x: 50, y: 12 },
        { x: 0, y: 0 },
        { x: 100, y: 0 },
      ),
    ).toMatchObject({
      distance: 12,
      closestX: 50,
      closestY: 0,
      t: 0.5,
    });
  });

  it('clamps distance checks to segment endpoints', () => {
    expect(
      pointToSegmentDistance(
        { x: 120, y: 5 },
        { x: 0, y: 0 },
        { x: 100, y: 0 },
      ),
    ).toMatchObject({
      closestX: 100,
      closestY: 0,
      t: 1,
    });
  });
});

describe('extractGraphData', () => {
  it('rejects explicit graph maps whose nodes or links are not arrays', () => {
    const result = extractGraphData(
      [
        {
          graph: {
            nodes: { length: 1, 0: { id: 'a' } },
            links: [],
          },
        },
      ],
      'name',
    );

    expect(result).toBeNull();
  });

  it('keeps Neo4j path metadata separate from node and relationship properties', () => {
    const result = extractGraphData(
      [
        {
          path: {
            nodes: [
              {
                id: 42,
                labels: ['GitHubDependabotAlert'],
                properties: { id: 'domain-alert-id', name: 'Alert A' },
              },
              {
                id: 43,
                labels: ['GitHubRepository'],
                properties: { id: 'domain-repo-id', name: 'repo-a' },
              },
            ],
            relationships: [
              {
                id: 7,
                type: 'AFFECTS',
                start_node_id: 42,
                end_node_id: 43,
                properties: { id: 'domain-rel-id' },
              },
            ],
          },
        },
      ],
      'name',
    );

    expect(result?.nodes[0]).toMatchObject({
      id: 'domain-alert-id',
      neo4j_id: 42,
      labels: ['GitHubDependabotAlert'],
      properties: { id: 'domain-alert-id', name: 'Alert A' },
      label: 'Alert A',
      group: 'GitHubDependabotAlert',
    });
    expect(result?.links[0]).toMatchObject({
      neo4j_id: 7,
      type: 'AFFECTS',
      source: 'domain-alert-id',
      target: 'domain-repo-id',
      properties: { id: 'domain-rel-id' },
    });
  });

  it('falls back to Neo4j internal id when a path node has no id property', () => {
    const result = extractGraphData(
      [
        {
          path: {
            nodes: [
              {
                id: 42,
                labels: ['GitHubDependabotAlert'],
                properties: { name: 'Alert A' },
              },
            ],
            relationships: [],
          },
        },
      ],
      'name',
    );

    expect(result?.nodes[0]).toMatchObject({
      id: 42,
      neo4j_id: 42,
      properties: { name: 'Alert A' },
    });
  });
});

describe('graphNodeHoverId', () => {
  it('prefers the property id for node hover text', () => {
    expect(
      graphNodeHoverId({
        id: 'rendered-id',
        neo4j_id: 42,
        properties: { id: 'property-id' },
      }),
    ).toBe('property-id');
  });

  it('falls back to Neo4j internal id when property id is unset', () => {
    expect(
      graphNodeHoverId({
        id: 'rendered-id',
        neo4j_id: 42,
        properties: {},
      }),
    ).toBe('42');
  });

  it('falls back to rendered id when no Neo4j internal id is available', () => {
    expect(
      graphNodeHoverId({
        id: 'rendered-id',
      }),
    ).toBe('rendered-id');
  });
});

describe('buildXyEdges', () => {
  it('draws relationship labels without a background box', () => {
    const result = buildXyEdges(
      [
        {
          source: 'a',
          target: 'b',
          type: 'REL_AB',
        },
      ],
      EDGE_COLOR,
    );

    expect(result[0].label).toBe('REL_AB');
    expect(result[0].type).toBe('relationship');
    expect(result[0].labelShowBg).toBe(false);
    expect(result[0].labelStyle).toMatchObject({
      color: EDGE_COLOR,
      fontSize: 11,
      fontWeight: 500,
      opacity: 1,
    });
    expect(result[0].style).toMatchObject({
      stroke: EDGE_COLOR,
      strokeWidth: 1.4,
      opacity: 1,
    });
  });

  it('uses the closest source and target handles when positions are available', () => {
    const result = buildXyEdges(
      [
        {
          source: 'a',
          target: 'b',
          type: 'REL_AB',
        },
      ],
      EDGE_COLOR,
      new Map([
        ['a', { x: 0, y: 0 }],
        ['b', { x: 100, y: 10 }],
      ]),
    );

    expect(result[0].sourceHandle).toBe('source-right');
    expect(result[0].targetHandle).toBe('target-left');
  });

  it('uses diagonal handles when the edge direction is diagonal', () => {
    const result = buildXyEdges(
      [
        {
          source: 'a',
          target: 'b',
          type: 'REL_AB',
        },
      ],
      EDGE_COLOR,
      new Map([
        ['a', { x: 0, y: 0 }],
        ['b', { x: 100, y: 100 }],
      ]),
    );

    expect(result[0].sourceHandle).toBe('source-bottom-right');
    expect(result[0].targetHandle).toBe('target-top-left');
  });
});

describe('closestEdgeHandles', () => {
  it('chooses horizontal handles for mostly horizontal edges', () => {
    expect(closestEdgeHandles({ x: 0, y: 0 }, { x: 100, y: 10 })).toEqual({
      sourceHandle: 'source-right',
      targetHandle: 'target-left',
    });
    expect(closestEdgeHandles({ x: 100, y: 0 }, { x: 0, y: 10 })).toEqual({
      sourceHandle: 'source-left',
      targetHandle: 'target-right',
    });
  });

  it('chooses vertical handles for mostly vertical edges', () => {
    expect(closestEdgeHandles({ x: 0, y: 0 }, { x: 10, y: 100 })).toEqual({
      sourceHandle: 'source-bottom',
      targetHandle: 'target-top',
    });
    expect(closestEdgeHandles({ x: 0, y: 100 }, { x: 10, y: 0 })).toEqual({
      sourceHandle: 'source-top',
      targetHandle: 'target-bottom',
    });
  });

  it('chooses diagonal handles for diagonal edges', () => {
    expect(closestEdgeHandles({ x: 0, y: 0 }, { x: 100, y: 100 })).toEqual({
      sourceHandle: 'source-bottom-right',
      targetHandle: 'target-top-left',
    });
    expect(closestEdgeHandles({ x: 0, y: 0 }, { x: -100, y: -100 })).toEqual({
      sourceHandle: 'source-top-left',
      targetHandle: 'target-bottom-right',
    });
  });

  it('chooses intermediate handles between cardinal and diagonal directions', () => {
    expect(closestEdgeHandles({ x: 0, y: 0 }, { x: 100, y: 40 })).toEqual({
      sourceHandle: 'source-right-lower',
      targetHandle: 'target-left-upper',
    });
    expect(closestEdgeHandles({ x: 0, y: 0 }, { x: 40, y: 100 })).toEqual({
      sourceHandle: 'source-bottom-right-lower',
      targetHandle: 'target-top-left-upper',
    });
  });
});

describe('relationshipLabelTransform', () => {
  it('rotates relationship labels along the edge line', () => {
    expect(relationshipLabelTransform(0, 0, 10, 10, 5, 5)).toContain(
      'rotate(45deg)',
    );
  });

  it('offsets relationship labels above the edge line', () => {
    expect(relationshipLabelTransform(0, 0, 10, 0, 5, 0)).toBe(
      'translate(-50%, -50%) translate(5px, -7.5px) rotate(0deg)',
    );
  });

  it('keeps labels readable on right-to-left edges', () => {
    expect(relationshipLabelTransform(10, 0, 0, 0, 5, 0)).toBe(
      'translate(-50%, -50%) translate(5px, -7.5px) rotate(0deg)',
    );
  });
});

describe('applyFocusOpacity', () => {
  describe('focusedId = null (clear focus)', () => {
    const result = applyFocusOpacity(NODES, EDGES, null, EDGE_COLOR);

    it('sets all nodes to full opacity', () => {
      for (const n of result.nodes) {
        expect(n.style?.opacity).toBe(1);
      }
    });

    it('sets all edges to full opacity', () => {
      for (const e of result.edges) {
        expect(e.style?.opacity).toBe(1);
      }
    });

    it('sets all edge labels and label backgrounds to full opacity', () => {
      for (const e of result.edges) {
        expect(e.labelStyle?.opacity).toBe(1);
        expect(e.labelBgStyle?.opacity).toBe(1);
      }
    });

    it('includes a transition on all nodes', () => {
      for (const n of result.nodes) {
        expect(n.style?.transition).toBe('opacity 0.2s');
      }
    });

    it('includes a transition on all edges', () => {
      for (const e of result.edges) {
        expect(e.style?.transition).toBe('opacity 0.2s');
      }
    });
  });

  describe('node focused ("a" — hub with two outgoing edges)', () => {
    const result = applyFocusOpacity(NODES, EDGES, 'a', EDGE_COLOR);

    it('keeps the focused node at full opacity', () => {
      const node = result.nodes.find((n) => n.id === 'a')!;
      expect(node.style?.opacity).toBe(1);
    });

    it('keeps direct neighbors (b, c) at full opacity', () => {
      const b = result.nodes.find((n) => n.id === 'b')!;
      const c = result.nodes.find((n) => n.id === 'c')!;
      expect(b.style?.opacity).toBe(1);
      expect(c.style?.opacity).toBe(1);
    });

    it('dims nodes not connected to focused node (d)', () => {
      const d = result.nodes.find((n) => n.id === 'd')!;
      expect(d.style?.opacity).toBe(0.35);
    });

    it('keeps edges directly connected to focused node at full opacity', () => {
      const e0 = result.edges.find((e) => e.id === 'edge-0')!;
      const e1 = result.edges.find((e) => e.id === 'edge-1')!;
      expect(e0.style?.opacity).toBe(1);
      expect(e1.style?.opacity).toBe(1);
    });

    it('dims edges not connected to focused node', () => {
      const e2 = result.edges.find((e) => e.id === 'edge-2')!;
      expect(e2.style?.opacity).toBe(0.35);
    });

    it('dims label and label background of non-connected edges', () => {
      const e2 = result.edges.find((e) => e.id === 'edge-2')!;
      expect(e2.labelStyle?.opacity).toBe(0.35);
      expect(e2.labelBgStyle?.opacity).toBe(0.35);
    });

    it('keeps label and label background of connected edges at full opacity', () => {
      const e0 = result.edges.find((e) => e.id === 'edge-0')!;
      expect(e0.labelStyle?.opacity).toBe(1);
      expect(e0.labelBgStyle?.opacity).toBe(1);
    });
  });

  describe('node focused ("e" — truly isolated node with no edges)', () => {
    // Use a separate graph that includes an isolated node 'e'
    const nodesWithIsolated = [...NODES, makeNode('e')];
    const result = applyFocusOpacity(nodesWithIsolated, EDGES, 'e', EDGE_COLOR);

    it('keeps isolated focused node at full opacity', () => {
      const e = result.nodes.find((n) => n.id === 'e')!;
      expect(e.style?.opacity).toBe(1);
    });

    it('dims all other nodes', () => {
      for (const n of result.nodes.filter((n) => n.id !== 'e')) {
        expect(n.style?.opacity).toBe(0.35);
      }
    });

    it('dims all edges since none are connected to the isolated node', () => {
      for (const e of result.edges) {
        expect(e.style?.opacity).toBe(0.35);
      }
    });
  });

  describe('edge focused ("edge-2": c → d)', () => {
    const result = applyFocusOpacity(NODES, EDGES, 'edge-2', EDGE_COLOR);

    it('keeps the source node (c) at full opacity', () => {
      const c = result.nodes.find((n) => n.id === 'c')!;
      expect(c.style?.opacity).toBe(1);
    });

    it('keeps the target node (d) at full opacity', () => {
      const d = result.nodes.find((n) => n.id === 'd')!;
      expect(d.style?.opacity).toBe(1);
    });

    it('dims nodes not part of the focused edge', () => {
      const a = result.nodes.find((n) => n.id === 'a')!;
      const b = result.nodes.find((n) => n.id === 'b')!;
      expect(a.style?.opacity).toBe(0.35);
      expect(b.style?.opacity).toBe(0.35);
    });

    it('keeps the focused edge at full opacity', () => {
      const e2 = result.edges.find((e) => e.id === 'edge-2')!;
      expect(e2.style?.opacity).toBe(1);
    });

    it('dims other edges', () => {
      const e0 = result.edges.find((e) => e.id === 'edge-0')!;
      const e1 = result.edges.find((e) => e.id === 'edge-1')!;
      expect(e0.style?.opacity).toBe(0.35);
      expect(e1.style?.opacity).toBe(0.35);
    });

    it('dims label and label background of non-focused edges', () => {
      const e0 = result.edges.find((e) => e.id === 'edge-0')!;
      expect(e0.labelStyle?.opacity).toBe(0.35);
      expect(e0.labelBgStyle?.opacity).toBe(0.35);
    });

    it('keeps label and label background of focused edge at full opacity', () => {
      const e2 = result.edges.find((e) => e.id === 'edge-2')!;
      expect(e2.labelStyle?.opacity).toBe(1);
      expect(e2.labelBgStyle?.opacity).toBe(1);
    });
  });

  describe('unknown focusedId (not a node or edge id)', () => {
    const result = applyFocusOpacity(
      NODES,
      EDGES,
      'does-not-exist',
      EDGE_COLOR,
    );

    it('returns the original node array unchanged', () => {
      expect(result.nodes).toBe(NODES);
    });

    it('returns the original edge array unchanged', () => {
      expect(result.edges).toBe(EDGES);
    });
  });

  describe('preserves existing style properties', () => {
    it('preserves stroke color on edges when clearing focus', () => {
      const result = applyFocusOpacity(NODES, EDGES, null, EDGE_COLOR);
      for (const e of result.edges) {
        expect(e.style?.stroke).toBe(EDGE_COLOR);
      }
    });

    it('preserves strokeWidth on edges when applying node focus', () => {
      const result = applyFocusOpacity(NODES, EDGES, 'a', EDGE_COLOR);
      for (const e of result.edges) {
        expect(e.style?.strokeWidth).toBe(1.5);
      }
    });

    it('preserves existing node style properties when dimming', () => {
      const nodesWithStyle = [
        makeNode('x', { background: 'red' }),
        makeNode('y'),
      ];
      const result = applyFocusOpacity(nodesWithStyle, [], 'y', EDGE_COLOR);
      const x = result.nodes.find((n) => n.id === 'x')!;
      expect(x.style?.background).toBe('red');
      expect(x.style?.opacity).toBe(0.35);
    });
  });
});

describe('placeEdgeLabels', () => {
  const at = (entries: Array<[string, number, number]>) =>
    new Map(entries.map(([id, x, y]) => [id, { x, y }]));

  it('gives edges between the same two nodes one label naming them all', () => {
    const placed = placeEdgeLabels(
      [
        { source: 'a', target: 'b', type: 'REST' },
        { source: 'b', target: 'a', type: 'REST' },
        { source: 'a', target: 'b', type: 'tools' },
      ],
      at([
        ['a', 0, 0],
        ['b', 400, 0],
      ]),
    );

    expect(placed.map((p) => p.label)).toEqual([
      'REST ×2 · tools',
      undefined,
      undefined,
    ]);
  });

  it('moves a label along its edge to clear one already placed', () => {
    // Two long edges crossing at their midpoints.
    const placed = placeEdgeLabels(
      [
        { source: 'a', target: 'b', type: 'first' },
        { source: 'c', target: 'd', type: 'second' },
      ],
      at([
        ['a', 0, 0],
        ['b', 600, 600],
        ['c', 600, 0],
        ['d', 0, 600],
      ]),
    );

    expect(placed.every((p) => !p.labelHidden)).toBe(true);
    expect(placed[0].labelT).not.toBe(placed[1].labelT);
  });

  it('holds back a label with no clear place rather than overprinting', () => {
    // Two short edges crossing between nodes that leave no room to move.
    const placed = placeEdgeLabels(
      [
        { source: 'a', target: 'b', type: 'schedules' },
        { source: 'c', target: 'd', type: 'queries' },
      ],
      at([
        ['a', 0, 0],
        ['b', 110, 110],
        ['c', 110, 0],
        ['d', 0, 110],
      ]),
    );

    expect(placed.filter((p) => p.labelHidden)).toHaveLength(1);
  });
});
