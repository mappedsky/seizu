import { useEffect, useRef } from 'react';
import { useTheme } from '@mui/material/styles';
import { brand } from 'src/theme/brand';

// Seizu mark geometry — viewBox 0 0 140 140, centre 70,70
const NODES = [
  { x: 40, y: 55, r: 3.0, role: 'blue' }, // A
  { x: 62, y: 42, r: 4.2, role: 'gold' }, // B — amber anchor
  { x: 86, y: 52, r: 3.0, role: 'white' }, // C
  { x: 100, y: 78, r: 3.4, role: 'blue' }, // D
  { x: 68, y: 78, r: 3.8, role: 'white' }, // E
  { x: 50, y: 98, r: 2.6, role: 'blue' }, // F
] as const;

const EDGES: [number, number][] = [
  [0, 1],
  [1, 2],
  [2, 3],
  [1, 4],
  [4, 3],
  [4, 5],
];

const RING_R = 54;
const RING_N = 28; // dash count
const RING_HALF = 2.0; // half-length of each dash (viewBox units)

// Gold anchor node position — used as aura centre
const AURA_CX = 62;
const AURA_CY = 42;

// Twinkling phase offsets follow constellation connection order
const TWINKLE_PHASES = (() => {
  const order = [1, 0, 2, 4, 3, 5];
  return NODES.map((_, i) => order.indexOf(i) * 0.62);
})();

const TWINKLE_SPEED = 5.6; // rad/sec
const SEC_PER_REV = 2.4; // ring chase period
const GLOW = 0.35; // bloom intensity (matches design)

// 4-point star path centred on (cx, cy) with outer radius R
function starPath(cx: number, cy: number, R: number): string {
  const i = R * 0.4;
  return (
    `M${cx} ${cy - R} L${cx + i} ${cy - i} L${cx + R} ${cy} ` +
    `L${cx + i} ${cy + i} L${cx} ${cy + R} L${cx - i} ${cy + i} ` +
    `L${cx - R} ${cy} L${cx - i} ${cy - i} Z`
  );
}

// Pre-compute the 28 ring-dash endpoints (static — no rotation)
const RING_DASHES = Array.from({ length: RING_N }, (_, i) => {
  const a = (i / RING_N) * 2 * Math.PI;
  const px = 70 + RING_R * Math.cos(a);
  const py = 70 + RING_R * Math.sin(a);
  const tx = -Math.sin(a);
  const ty = Math.cos(a);
  return {
    x1: (px - RING_HALF * tx).toFixed(2),
    y1: (py - RING_HALF * ty).toFixed(2),
    x2: (px + RING_HALF * tx).toFixed(2),
    y2: (py + RING_HALF * ty).toFixed(2),
  };
});

const TICKS: [number, number, number, number][] = [
  [70, 6, 70, 16],
  [70, 124, 70, 134],
  [6, 70, 16, 70],
  [124, 70, 134, 70],
];

let _uid = 0;

interface ConstellationSpinnerProps {
  size?: number;
}

export default function ConstellationSpinner({
  size = 96,
}: ConstellationSpinnerProps) {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';

  const ringColor = isDark ? brand.starlight : brand.starlightDark;
  const goldColor = isDark ? brand.ember : brand.emberDark;
  const blueColor = isDark ? brand.starlight : brand.starlightDark;
  const whiteColor = isDark ? '#ffffff' : '#0a0f22';

  function nodeColor(role: string): string {
    if (role === 'gold') return goldColor;
    if (role === 'white') return whiteColor;
    return blueColor;
  }

  // Stable per-instance SVG ID prefix to avoid defs collisions
  const idRef = useRef<string | null>(null);
  if (!idRef.current) idRef.current = `csz${++_uid}`;
  const id = idRef.current;

  // Refs to the SVG elements driven by the rAF loop
  const dashRef = useRef<(SVGLineElement | null)[]>([]);
  const sparkRef = useRef<(SVGPathElement | null)[]>([]);
  const auraRef = useRef<SVGCircleElement | null>(null);
  const rafRef = useRef<number | null>(null);
  const t0Ref = useRef<number | null>(null);

  useEffect(() => {
    t0Ref.current = null; // reset on mount/remount

    function tick(now: number) {
      if (!t0Ref.current) t0Ref.current = now;
      const tsec = (now - t0Ref.current) / 1000;

      // Comet-chase brightness traveling around the static dashes
      const head = (tsec * (360 / SEC_PER_REV)) % 360;
      for (let i = 0; i < RING_N; i++) {
        const el = dashRef.current[i];
        if (!el) continue;
        const dashAngle = (i / RING_N) * 360;
        const frac = ((((head - dashAngle) % 360) + 360) % 360) / 360;
        el.setAttribute(
          'opacity',
          (0.1 + 0.9 * Math.pow(1 - frac, 1.7)).toFixed(3),
        );
      }

      // Twinkling sparkles — staggered sine-wave scale + gentle wobble
      let goldTw = 0.5;
      for (let i = 0; i < NODES.length; i++) {
        const el = sparkRef.current[i];
        if (!el) continue;
        const tw =
          0.5 + 0.5 * Math.sin(tsec * TWINKLE_SPEED + TWINKLE_PHASES[i]);
        if (NODES[i].role === 'gold') goldTw = tw;
        const sc = (0.74 + 0.26 * tw).toFixed(3);
        const wob = (7 * Math.sin(tsec * 0.9 + TWINKLE_PHASES[i])).toFixed(2);
        const n = NODES[i];
        el.setAttribute(
          'transform',
          `translate(${n.x} ${n.y}) rotate(${wob}) scale(${sc}) translate(${-n.x} ${-n.y})`,
        );
        el.setAttribute('opacity', (0.5 + 0.5 * tw).toFixed(3));
      }

      // Soft aura glow tracks the gold sparkle's pulse
      if (auraRef.current) {
        auraRef.current.setAttribute(
          'opacity',
          (0.4 * GLOW * (0.5 + 0.5 * goldTw)).toFixed(3),
        );
      }

      rafRef.current = requestAnimationFrame(tick);
    }

    rafRef.current = requestAnimationFrame(tick);
    return () => {
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
    };
  }, []);

  const stdDev = (1.4 + GLOW * 2.6).toFixed(2);

  return (
    <svg
      viewBox="0 0 140 140"
      width={size}
      height={size}
      role="img"
      aria-label="Loading"
      style={{ display: 'block', overflow: 'visible' }}
    >
      <defs>
        <filter id={id} x="-60%" y="-60%" width="220%" height="220%">
          <feGaussianBlur stdDeviation={stdDev} result="b" />
          <feMerge>
            <feMergeNode in="b" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
        <radialGradient id={`${id}a`}>
          <stop offset="0%" stopColor={goldColor} stopOpacity={0.55} />
          <stop offset="60%" stopColor={goldColor} stopOpacity={0.12} />
          <stop offset="100%" stopColor={goldColor} stopOpacity={0} />
        </radialGradient>
      </defs>

      {/* Aura — radial glow behind the amber anchor node */}
      <circle
        ref={auraRef}
        cx={AURA_CX}
        cy={AURA_CY}
        r={56}
        fill={`url(#${id}a)`}
        opacity={0}
      />

      {/* Ring: faint base circle */}
      <circle
        cx={70}
        cy={70}
        r={RING_R}
        fill="none"
        stroke={ringColor}
        strokeWidth={2.8}
        opacity={0.2}
      />

      {/* Ring: 28 dashes animated with comet-chase opacity */}
      {RING_DASHES.map((d, i) => (
        <line
          key={d.x1}
          ref={(el) => {
            dashRef.current[i] = el;
          }}
          x1={d.x1}
          y1={d.y1}
          x2={d.x2}
          y2={d.y2}
          stroke={ringColor}
          strokeWidth={3.6}
          strokeLinecap="round"
          opacity={0.5}
        />
      ))}

      {/* Cardinal ticks at 12/3/6/9 */}
      {TICKS.map(([x1, y1, x2, y2]) => (
        <line
          key={`${x1}-${y1}`}
          x1={x1}
          y1={y1}
          x2={x2}
          y2={y2}
          stroke={ringColor}
          strokeWidth={3.2}
          strokeLinecap="round"
          opacity={0.6}
        />
      ))}

      {/* Constellation edges */}
      <g
        stroke={ringColor}
        strokeWidth={2.6}
        strokeLinecap="round"
        opacity={0.45}
      >
        {EDGES.map(([a, b]) => (
          <line
            key={`${a}-${b}`}
            x1={NODES[a].x}
            y1={NODES[a].y}
            x2={NODES[b].x}
            y2={NODES[b].y}
          />
        ))}
      </g>

      {/* Twinkling sparkle at each constellation node */}
      <g filter={`url(#${id})`}>
        {NODES.map((n, i) => (
          <path
            key={`${n.x}-${n.y}`}
            ref={(el) => {
              sparkRef.current[i] = el;
            }}
            d={starPath(n.x, n.y, n.r * 2.5)}
            fill={nodeColor(n.role)}
          />
        ))}
      </g>
    </svg>
  );
}
