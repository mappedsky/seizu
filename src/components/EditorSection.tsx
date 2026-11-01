import type { ReactNode } from 'react';
import { Box, Typography } from '@mui/material';
import { fontFamilyMono } from 'src/theme/typography';

/**
 * One titled block of a long editor form: a heading, an optional line saying
 * what the block is for, an optional trailing action, then its controls.
 */
export function EditorSection({
  title,
  description,
  action,
  children,
}: {
  title: string;
  description?: ReactNode;
  action?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <Box
      component="section"
      sx={{ borderTop: 1, borderColor: 'divider', pt: { xs: 2.5, md: 3 } }}
    >
      <Box
        sx={{
          alignItems: 'flex-start',
          display: 'flex',
          flexWrap: 'wrap',
          gap: 1.5,
          justifyContent: 'space-between',
          mb: children ? 2 : 0,
        }}
      >
        <Box sx={{ flex: '1 1 240px', minWidth: 0 }}>
          <Typography variant="h4" component="h3">
            {title}
          </Typography>
          {description && (
            <Typography
              variant="body2"
              color="text.secondary"
              sx={{ maxWidth: '68ch', mt: 0.5 }}
            >
              {description}
            </Typography>
          )}
        </Box>
        {action && <Box sx={{ flexShrink: 0 }}>{action}</Box>}
      </Box>
      {children}
    </Box>
  );
}

/**
 * A derived, read-only identity value, labelled for assistive technology so
 * it can be found by name the way an input would be.
 */
export function IdentityFact({
  label,
  value,
}: {
  label: string;
  value: string;
}) {
  return (
    <Box role="group" aria-label={label} sx={{ minWidth: 0 }}>
      <Typography
        variant="caption"
        component="div"
        sx={{ color: 'text.secondary', fontWeight: 600 }}
      >
        {label}
      </Typography>
      <Typography
        variant="body2"
        component="div"
        sx={{ fontFamily: fontFamilyMono, overflowWrap: 'anywhere' }}
      >
        {value}
      </Typography>
    </Box>
  );
}

/** A wrapping row of identity facts under an editor's title. */
export function IdentityFacts({ children }: { children: ReactNode }) {
  return (
    <Box
      sx={{
        columnGap: { xs: 3, md: 4 },
        display: 'flex',
        flexWrap: 'wrap',
        mt: 1.5,
        rowGap: 1.5,
      }}
    >
      {children}
    </Box>
  );
}
