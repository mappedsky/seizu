import { fireEvent, render, screen } from '@testing-library/react';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import QueryValidationBadge from '../QueryValidationBadge';

const theme = createTheme();

test('shows a query execution error returned by the server', () => {
  render(
    <ThemeProvider theme={theme}>
      <QueryValidationBadge
        errors={['Invalid call signature for datetime()']}
        warnings={[]}
      />
    </ThemeProvider>,
  );

  fireEvent.click(screen.getByRole('button', { name: /query errors/i }));

  expect(screen.getByText('Query Issues')).toBeInTheDocument();
  expect(
    screen.getByText('Invalid call signature for datetime()'),
  ).toBeInTheDocument();
});
