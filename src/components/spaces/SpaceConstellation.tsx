import { useMemo } from 'react';
import { Box } from '@mui/material';
import { useTheme } from '@mui/material/styles';
import { brand, tones } from 'src/theme/brand';

/**
 * A space's mark: the brand's dashed orbit with one star per member report,
 * stars of the same sub-space joined into one figure.
 *
 * It is a reading of the space's shape, not decoration: an empty space is an
 * empty orbit, a large one is dense, and sub-spaces show as separate figures.
 * Layout is seeded by the space id so a space keeps its mark between visits.
 */

const VIEW = 100;
const CENTER = VIEW / 2;
const ORBIT_R = 44;
const FIELD_R = 32;
const MAX_STARS = 14;
const RING_DASHES = 24;

interface SpaceConstellationProps {
  seed: string;
  /** One entry per member report: its sub-space id, or null if ungrouped. */
  groups: (string | null | undefined)[];
  /** Index into `groups` of the anchor star (the overview), if any. */
  anchorIndex?: number | null;
  /** Pixels, or a breakpoint map such as `{ xs: 40, sm: 56 }`. */
  size?: number | Partial<Record<'xs' | 'sm' | 'md' | 'lg' | 'xl', number>>;
}

function hash(value: string): number {
  let h = 2166136261;
  for (let i = 0; i < value.length; i += 1) {
    h ^= value.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

function rng(seed: number): () => number {
  let state = seed || 1;
  return () => {
    // mulberry32
    state = (state + 0x6d2b79f5) | 0;
    let t = Math.imul(state ^ (state >>> 15), 1 | state);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

interface Star {
  x: number;
  y: number;
  r: number;
  group: string;
}

function layout(seed: string, groups: string[]): Star[] {
  const random = rng(hash(seed));
  const shown = groups.slice(0, MAX_STARS);
  const keys = [...new Set(shown)];
  // Each sub-space gets its own sector of the field, so its stars cluster
  // into a figure rather than interleaving with another group's.
  const offset = random() * Math.PI * 2;
  const sector = (Math.PI * 2) / Math.max(keys.length, 1);
  const stars: Star[] = [];
  shown.forEach((group) => {
    const k = keys.indexOf(group);
    const spread = keys.length === 1 ? Math.PI * 2 : sector * 0.8;
    const angle = offset + k * sector + (random() - 0.5) * spread;
    const radius =
      keys.length === 1
        ? FIELD_R * Math.sqrt(random())
        : FIELD_R * (0.35 + 0.65 * random());
    let x = CENTER + Math.cos(angle) * radius;
    let y = CENTER + Math.sin(angle) * radius;
    // Nudge away from stars already placed so marks never merge into blobs.
    for (let pass = 0; pass < 6; pass += 1) {
      const near = stars.find((s) => Math.hypot(s.x - x, s.y - y) < 11);
      if (!near) break;
      const a = Math.atan2(y - near.y, x - near.x) || random() * Math.PI * 2;
      x = Math.min(Math.max(x + Math.cos(a) * 6, 16), VIEW - 16);
      y = Math.min(Math.max(y + Math.sin(a) * 6, 16), VIEW - 16);
    }
    stars.push({ x, y, r: 3.4 + random() * 1.8, group });
  });
  return stars;
}

/** Join each group's stars as a path, nearest unvisited neighbour first. */
function edges(stars: Star[]): [Star, Star][] {
  const out: [Star, Star][] = [];
  const byGroup = new Map<string, Star[]>();
  stars.forEach((s) =>
    byGroup.set(s.group, [...(byGroup.get(s.group) ?? []), s]),
  );
  byGroup.forEach((members) => {
    const left = [...members];
    let current = left.shift();
    while (current && left.length) {
      const from: Star = current;
      left.sort(
        (a, b) =>
          Math.hypot(a.x - from.x, a.y - from.y) -
          Math.hypot(b.x - from.x, b.y - from.y),
      );
      const next = left.shift()!;
      out.push([from, next]);
      current = next;
    }
  });
  return out;
}

export default function SpaceConstellation({
  seed,
  groups,
  anchorIndex = null,
  size = 44,
}: SpaceConstellationProps) {
  const theme = useTheme();
  const dark = theme.palette.mode === 'dark';
  // Keyed on content, not identity: callers derive `groups` per render.
  const signature = JSON.stringify(groups.map((g) => g ?? ''));
  const stars = useMemo(
    () => layout(seed, JSON.parse(signature) as string[]),
    [seed, signature],
  );
  const lines = useMemo(() => edges(stars), [stars]);

  const ring = dark ? 'rgba(143,180,255,0.55)' : tones.starlight[600];
  const line = dark ? 'rgba(143,180,255,0.4)' : tones.starlight[300];
  const star = dark ? tones.starlight[200] : tones.starlight[700];
  const anchor = dark ? brand.ember : tones.ember[600];
  const dashLength = (2 * Math.PI * ORBIT_R) / RING_DASHES / 2;

  return (
    <Box
      component="svg"
      viewBox={`0 0 ${VIEW} ${VIEW}`}
      aria-hidden
      sx={{ width: size, height: size, flexShrink: 0, display: 'block' }}
    >
      <circle
        cx={CENTER}
        cy={CENTER}
        r={ORBIT_R}
        fill="none"
        stroke={ring}
        strokeWidth={2.2}
        strokeDasharray={`${dashLength} ${dashLength}`}
        strokeLinecap="round"
      />
      {lines.map(([a, b]) => (
        <line
          key={`${a.x},${a.y}-${b.x},${b.y}`}
          x1={a.x}
          y1={a.y}
          x2={b.x}
          y2={b.y}
          stroke={line}
          strokeWidth={1.6}
        />
      ))}
      {stars.map((s, i) => (
        <circle
          key={`${s.x},${s.y}`}
          cx={s.x}
          cy={s.y}
          r={i === anchorIndex ? s.r + 1.2 : s.r}
          fill={i === anchorIndex ? anchor : star}
        />
      ))}
    </Box>
  );
}
