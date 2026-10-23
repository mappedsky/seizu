import Box from '@mui/material/Box';
import { useTheme } from '@mui/material/styles';
import { severityColor, type Severity } from 'src/theme/severity';

interface SeverityLabelProps {
  severity: Severity;
  /** The value as the data spells it; shown unchanged. */
  children: string;
}

/**
 * A severity value in a table cell: the value as written, led by a dot in the
 * level's color. The text carries the meaning; the dot lets a column be
 * scanned for the urgent rows, and Critical is also set in bold.
 */
export default function SeverityLabel({
  severity,
  children,
}: SeverityLabelProps) {
  const theme = useTheme();
  return (
    <Box
      component="span"
      sx={{
        alignItems: 'center',
        display: 'inline-flex',
        fontWeight: severity === 'critical' ? 600 : undefined,
        gap: 0.75,
        minWidth: 0,
      }}
    >
      <Box
        component="span"
        aria-hidden
        sx={{
          bgcolor: severityColor(severity, theme.palette.mode),
          borderRadius: '50%',
          flexShrink: 0,
          height: 8,
          width: 8,
        }}
      />
      <Box
        component="span"
        sx={{ overflow: 'hidden', textOverflow: 'ellipsis' }}
      >
        {children}
      </Box>
    </Box>
  );
}
