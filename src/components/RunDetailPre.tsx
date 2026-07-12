import { Box, Typography } from '@mui/material';

/**
 * Labeled preformatted block for run/transcript detail output (tool results,
 * failure messages, argument dumps). Shared by the scheduled chat and
 * scheduled query run views.
 */
function RunDetailPre({ label, value }: { label: string; value: string }) {
  return (
    <Box sx={{ mb: 0.5 }}>
      <Typography variant="caption" sx={{ color: 'text.secondary' }}>
        {label}
      </Typography>
      <Box
        component="pre"
        sx={{
          bgcolor: 'action.hover',
          borderRadius: 1,
          fontFamily: 'monospace',
          fontSize: 12,
          m: 0,
          maxHeight: 240,
          overflow: 'auto',
          p: 1,
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
        }}
      >
        {value}
      </Box>
    </Box>
  );
}

export default RunDetailPre;
