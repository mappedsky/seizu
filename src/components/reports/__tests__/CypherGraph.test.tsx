import { type Edge, type Node } from '@xyflow/react';
import {
  applyFocusOpacity,
  applyGroupFocus,
  dotDiameter,
  placeNodeLabels,
  buildXyEdges,
  placeEdgeLabels,
  relationshipTypes,
  applyTypeFocus,
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
      strokeWidth: 1.2,
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

  it('holds back a label longer than the line between its dots', () => {
    const placed = placeEdgeLabels(
      [
        { source: 'a', target: 'b', type: 'HAS_DEPENDENCY' },
        { source: 'c', target: 'd', type: 'HAS_DEPENDENCY' },
      ],
      at([
        ['a', 0, 0],
        ['b', 60, 0],
        ['c', 0, 200],
        ['d', 300, 200],
      ]),
    );

    expect(placed.map((p) => p.labelHidden)).toEqual([true, false]);
  });
});

describe('relationshipTypes', () => {
  it('lists types most common first, each with its own stroke', () => {
    const types = relationshipTypes([
      { source: 'a', target: 'b', type: 'OWNER' },
      { source: 'a', target: 'c', type: 'REQUIRES' },
      { source: 'a', target: 'd', type: 'REQUIRES' },
    ]);
    expect(types.map((t) => [t.type, t.count])).toEqual([
      ['REQUIRES', 2],
      ['OWNER', 1],
    ]);
    expect(types[0].dash).toBeUndefined();
    expect(types[1].dash).toBeDefined();
  });

  it('draws a single type solid', () => {
    expect(
      relationshipTypes([{ source: 'a', target: 'b', type: 'OWNER' }])[0].dash,
    ).toBeUndefined();
  });
});

describe('dotDiameter', () => {
  it('grows with degree, by square root, within its bounds', () => {
    expect(dotDiameter(0)).toBe(14);
    expect(dotDiameter(4)).toBeGreaterThan(dotDiameter(1));
    expect(dotDiameter(16) - dotDiameter(4)).toBeLessThan(
      dotDiameter(4) - dotDiameter(0) + 1,
    );
    expect(dotDiameter(10_000)).toBe(40);
  });
});

describe('placeNodeLabels', () => {
  const centres = (entries: Array<[string, number, number]>) =>
    new Map(entries.map(([id, x, y]) => [id, { x, y }]));

  it('names every star that has room, to its right first', () => {
    const placed = placeNodeLabels(
      [
        { id: 'a', label: 'FastAPI', degree: 3 },
        { id: 'b', label: 'Report store', degree: 1 },
      ],
      centres([
        ['a', 0, 0],
        ['b', 0, 200],
      ]),
      new Map(),
    );
    expect(placed.get('a')?.side).toBe('right');
    expect(placed.get('b')?.side).toBe('right');
  });

  it('lets the less connected name give way in a crowd', () => {
    // The hub and the leaf place first; the star crowded between them finds
    // its right side on the leaf's dot and its left on the hub's name.
    const placed = placeNodeLabels(
      [
        { id: 'hub', label: 'severity-hub', degree: 9 },
        { id: 'leaf', label: 'leaf-node-name', degree: 1 },
        { id: 'mid', label: 'middle-name', degree: 0 },
      ],
      centres([
        ['hub', 0, 0],
        ['leaf', 180, 0],
        ['mid', 110, 0],
      ]),
      new Map(),
    );
    expect(placed.get('hub')?.side).toBe('right');
    expect(placed.get('mid')?.side).toBeNull();
  });
});

describe('applyGroupFocus', () => {
  const nodes = [
    { id: 'a', position: { x: 0, y: 0 }, data: { group: 'CRITICAL' } },
    { id: 'b', position: { x: 0, y: 0 }, data: { group: 'CRITICAL' } },
    { id: 'c', position: { x: 0, y: 0 }, data: { group: 'LOW' } },
  ] as Node[];
  const edges = [
    { id: 'e1', source: 'a', target: 'b' },
    { id: 'e2', source: 'a', target: 'c' },
  ] as Edge[];

  it('lights one group and the relationships inside it', () => {
    const { nodes: n, edges: e } = applyGroupFocus(
      nodes,
      edges,
      'CRITICAL',
      EDGE_COLOR,
    );
    expect(n.map((x) => x.data.focused)).toEqual([true, true, false]);
    expect(n[2].style?.opacity).toBe(0.35);
    expect(e.map((x) => x.data?.focused)).toEqual([true, false]);
  });

  it('clears with no group', () => {
    const { nodes: n } = applyGroupFocus(nodes, edges, null, EDGE_COLOR);
    expect(n.every((x) => x.style?.opacity === 1)).toBe(true);
  });
});

describe('applyTypeFocus', () => {
  const nodes = [
    { id: 'a', position: { x: 0, y: 0 }, data: {} },
    { id: 'b', position: { x: 0, y: 0 }, data: {} },
    { id: 'c', position: { x: 0, y: 0 }, data: {} },
  ] as Node[];
  const edges = [
    { id: 'e1', source: 'a', target: 'b', data: { relType: 'OWNER' } },
    { id: 'e2', source: 'a', target: 'c', data: { relType: 'REQUIRES' } },
  ] as Edge[];

  it('lights one type and the nodes it joins, with its labels quiet', () => {
    const { nodes: n, edges: e } = applyTypeFocus(
      nodes,
      edges,
      'OWNER',
      EDGE_COLOR,
    );
    expect(n.map((x) => x.data.focused)).toEqual([true, true, false]);
    expect(e.map((x) => x.data?.focused)).toEqual([true, false]);
    expect(e[0].data?.labelMuted).toBe(true);
    expect(e[1].style?.opacity).toBe(0.35);
  });

  it('clears with no type, unmuting the labels', () => {
    const { edges: e } = applyTypeFocus(nodes, edges, null, EDGE_COLOR);
    expect(e.every((x) => x.data?.labelMuted === false)).toBe(true);
  });
});

describe('applyFocusOpacity on a hub', () => {
  it('keeps a hub from plating more labels than can be read', () => {
    const spokes = Array.from({ length: 13 }, (_, i) => `n${i}`);
    const nodes = ['hub', ...spokes].map((id) => ({
      id,
      position: { x: 0, y: 0 },
      data: {},
    })) as Node[];
    const edges = spokes.map((id, i) => ({
      id: `e${i}`,
      source: 'hub',
      target: id,
      data: { labelHidden: i > 0 },
    })) as Edge[];

    const { edges: e } = applyFocusOpacity(nodes, edges, 'hub', EDGE_COLOR);
    expect(e[0].data?.labelMuted).toBe(false);
    expect(e.slice(1).every((x) => x.data?.labelMuted === true)).toBe(true);
  });
});
