import PageTitle from 'src/components/PageTitle';
import { Box, Button, Container, Typography } from '@mui/material';

function LoggedOut() {
  return (
    <>
      <PageTitle>Logged out | Seizu</PageTitle>
      <Box
        sx={{
          backgroundColor: 'background.default',
          display: 'flex',
          flexDirection: 'column',
          height: '100%',
          justifyContent: 'center',
        }}
      >
        <Container maxWidth="sm">
          <Typography align="center" color="textPrimary" variant="h2">
            You are logged out
          </Typography>
          <Typography
            align="center"
            color="text.secondary"
            variant="body1"
            sx={{ mt: 2 }}
          >
            Your Seizu session has ended.
          </Typography>
          <Box sx={{ display: 'flex', justifyContent: 'center', mt: 4 }}>
            <Button href="/app/dashboard" variant="contained">
              Sign in
            </Button>
          </Box>
        </Container>
      </Box>
    </>
  );
}

export default LoggedOut;
