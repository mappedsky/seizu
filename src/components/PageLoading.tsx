import { Box } from '@mui/material';
import ConstellationSpinner from 'src/components/ConstellationSpinner';

/**
 * Stands in for a page whose code has not arrived yet, inside the layout's
 * chrome. The same centered spinner pages show while their own data loads,
 * so a cold load reads as one wait rather than two different ones.
 */
export default function PageLoading() {
  return (
    <Box
      role="status"
      aria-label="Loading page"
      sx={{ display: 'flex', justifyContent: 'center', p: 4 }}
    >
      <ConstellationSpinner size={48} />
    </Box>
  );
}
