import { Box, ButtonBase, Tooltip, Typography } from '@mui/material';
import { alpha, useTheme } from '@mui/material/styles';
import AddIcon from '@mui/icons-material/Add';
import { hueFor } from 'src/theme/brand';
import { fontFamilyMono } from 'src/theme/typography';

export interface PackageTreeNode {
  /** Package path of the file this node edits; the selection key. */
  path: string;
  title: string;
  /** Monospace identity line: the file or the skill id. */
  identity: string;
  /** Short facts after the identity, such as "2 files". */
  meta?: string[];
  /** Full name shown on hover, for identities the row truncates. */
  tooltip?: string;
  /** `new` and `modified` mark staged edits that are not yet published. */
  change?: 'new' | 'modified' | null;
  errors?: number;
  warnings?: number;
}

const NODE = 12;
// The orbit line and every node share this x so the line threads the nodes.
const AXIS = 22;

/**
 * The package as one constellation: the manifest is the anchor star and each
 * skill a node on the same orbit line, in package order.
 */
export default function PluginPackageTree({
  anchor,
  skills,
  selectedPath,
  onSelect,
  onAddSkill,
  addDisabled,
}: {
  anchor: PackageTreeNode;
  skills: PackageTreeNode[];
  selectedPath: string;
  onSelect: (path: string) => void;
  onAddSkill: () => void;
  addDisabled?: boolean;
}) {
  const theme = useTheme();
  const line = alpha(theme.palette.primary.main, 0.28);

  return (
    <Box
      component="nav"
      aria-label="Package contents"
      sx={{ position: 'relative', py: 1 }}
    >
      <Box
        aria-hidden
        sx={{
          position: 'absolute',
          left: AXIS,
          top: 30,
          bottom: 30,
          borderLeft: `1px solid ${line}`,
        }}
      />
      <TreeRow
        node={anchor}
        anchor
        selected={selectedPath === anchor.path}
        onSelect={onSelect}
      />
      {skills.map((node) => (
        <TreeRow
          key={node.path}
          node={node}
          selected={selectedPath === node.path}
          onSelect={onSelect}
        />
      ))}
      <ButtonBase
        onClick={onAddSkill}
        disabled={addDisabled}
        aria-label="Add skill"
        sx={{
          alignItems: 'center',
          borderRadius: 1,
          color: 'primary.main',
          display: 'flex',
          gap: 1.5,
          justifyContent: 'flex-start',
          minHeight: 44,
          mx: 0.5,
          pl: `${AXIS - 4 - 10}px`,
          pr: 1.5,
          transition: theme.transitions.create('background-color', {
            duration: 150,
          }),
          width: 'calc(100% - 8px)',
          '&:hover': { bgcolor: alpha(theme.palette.primary.main, 0.08) },
          '&.Mui-focusVisible': {
            outline: `2px solid ${theme.palette.primary.main}`,
            outlineOffset: -2,
          },
          '&.Mui-disabled': { color: 'text.disabled' },
        }}
      >
        <Box
          aria-hidden
          sx={{
            alignItems: 'center',
            bgcolor: 'background.default',
            border: '1px dashed currentColor',
            borderRadius: '50%',
            display: 'flex',
            height: 20,
            justifyContent: 'center',
            position: 'relative',
            width: 20,
          }}
        >
          <AddIcon sx={{ fontSize: 14 }} />
        </Box>
        <Typography variant="body2" sx={{ fontWeight: 600 }}>
          Add skill
        </Typography>
      </ButtonBase>
    </Box>
  );
}

function TreeRow({
  node,
  anchor = false,
  selected,
  onSelect,
}: {
  node: PackageTreeNode;
  anchor?: boolean;
  selected: boolean;
  onSelect: (path: string) => void;
}) {
  const theme = useTheme();
  const primary = theme.palette.primary.main;
  const ember = hueFor('ember', theme.palette.mode);
  const hasErrors = (node.errors ?? 0) > 0;
  const hasWarnings = (node.warnings ?? 0) > 0;
  // Amber marks a staged edit, as it does in the editor's header; the anchor
  // is told apart by being filled, not by its color.
  const tone = hasErrors
    ? theme.palette.error.main
    : node.change
      ? ember
      : primary;

  const row = (
    <ButtonBase
      onClick={() => onSelect(node.path)}
      aria-current={selected ? 'page' : undefined}
      sx={{
        alignItems: 'flex-start',
        bgcolor: selected ? alpha(primary, 0.1) : 'transparent',
        borderRadius: 1,
        display: 'flex',
        gap: 1.5,
        justifyContent: 'flex-start',
        mx: 0.5,
        pl: `${AXIS - 4 - NODE / 2}px`,
        pr: 1.5,
        py: 1.25,
        textAlign: 'left',
        transition: theme.transitions.create('background-color', {
          duration: 150,
        }),
        width: 'calc(100% - 8px)',
        '&:hover': {
          bgcolor: alpha(primary, selected ? 0.14 : 0.06),
        },
        '&.Mui-focusVisible': {
          outline: `2px solid ${primary}`,
          outlineOffset: -2,
        },
      }}
    >
      <Box
        aria-hidden
        sx={{
          bgcolor: selected || anchor ? tone : 'background.default',
          border: `2px solid ${tone}`,
          borderRadius: '50%',
          flexShrink: 0,
          height: NODE,
          mt: '5px',
          position: 'relative',
          transition: theme.transitions.create(['background-color'], {
            duration: 150,
          }),
          width: NODE,
        }}
      />
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Box sx={{ alignItems: 'center', display: 'flex', gap: 1 }}>
          <Typography
            variant="body2"
            noWrap
            sx={{
              color: selected ? 'text.primary' : 'text.secondary',
              flex: 1,
              fontWeight: selected ? 700 : 600,
              minWidth: 0,
            }}
          >
            {node.title}
          </Typography>
          {node.change && (
            <Typography
              component="span"
              variant="caption"
              sx={{
                color: ember,
                flexShrink: 0,
                fontWeight: 700,
              }}
            >
              {node.change === 'new' ? 'New' : 'Edited'}
            </Typography>
          )}
        </Box>
        <Typography
          variant="caption"
          noWrap
          component="div"
          sx={{ color: 'text.secondary', fontFamily: fontFamilyMono }}
        >
          {node.identity}
        </Typography>
        {(node.meta?.length || hasErrors || hasWarnings) && (
          <Box
            sx={{
              columnGap: 1,
              display: 'flex',
              flexWrap: 'wrap',
              mt: 0.25,
            }}
          >
            {hasErrors && (
              <Typography variant="caption" sx={{ color: 'error.main' }}>
                {node.errors} {node.errors === 1 ? 'error' : 'errors'}
              </Typography>
            )}
            {hasWarnings && (
              <Typography variant="caption" sx={{ color: 'warning.main' }}>
                {node.warnings} {node.warnings === 1 ? 'warning' : 'warnings'}
              </Typography>
            )}
            {node.meta?.map((fact) => (
              <Typography
                key={fact}
                variant="caption"
                sx={{ color: 'text.secondary' }}
              >
                {fact}
              </Typography>
            ))}
          </Box>
        )}
      </Box>
    </ButtonBase>
  );

  return node.tooltip ? (
    <Tooltip
      title={node.tooltip}
      placement="right"
      enterDelay={600}
      describeChild
    >
      {row}
    </Tooltip>
  ) : (
    row
  );
}
