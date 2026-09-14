import PageTitle from 'src/components/PageTitle';
import { Link as RouterLink } from 'react-router-dom';
import { Box, Button, Container, Typography } from '@mui/material';

function NotFound() {
  return (
    <>
      <PageTitle>404 | Seizu</PageTitle>
      <Box
        sx={{
          alignItems: 'center',
          display: 'flex',
          flexDirection: 'column',
          height: '100%',
          justifyContent: 'center',
          p: 3,
        }}
      >
        <Container maxWidth="sm" sx={{ textAlign: 'center' }}>
          <Box
            component="img"
            alt=""
            src="/static/images/astronaut.png"
            sx={(theme) => ({
              width: { xs: 240, sm: 300 },
              maxWidth: '100%',
              height: 'auto',
              mb: 2,
              // The sketch is dark ink on a transparent background — invert it
              // on dark surfaces so it reads as a light drawing on the deep
              // navy canvas instead of a bright blob.
              ...(theme.palette.mode === 'dark' && { filter: 'invert(1)' }),
            })}
          />
          <Typography color="text.primary" variant="h1" sx={{ mb: 1 }}>
            404
          </Typography>
          <Typography color="text.primary" variant="h4" sx={{ mb: 2 }}>
            Lost in space
          </Typography>
          <Typography color="text.secondary" variant="body1" sx={{ mb: 4 }}>
            We couldn&apos;t find that page. It may have moved, or the link may
            be incomplete.
          </Typography>
          <Button
            component={RouterLink}
            to="/app/dashboard"
            variant="contained"
          >
            Back to dashboard
          </Button>
        </Container>
      </Box>
    </>
  );
}

export default NotFound;
