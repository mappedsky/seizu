import { render, screen, cleanup } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import NotFound from 'src/pages/NotFound';

const renderWithMode = (mode: 'light' | 'dark') =>
  render(
    <ThemeProvider theme={createTheme({ palette: { mode } })}>
      <MemoryRouter>
        <NotFound />
      </MemoryRouter>
    </ThemeProvider>,
  );

describe('NotFound', () => {
  afterEach(cleanup);

  it('renders the 404 heading and helper copy', () => {
    renderWithMode('dark');
    expect(screen.getByText('404')).toBeInTheDocument();
    expect(screen.getByText('Lost in space')).toBeInTheDocument();
  });

  it('links back to the dashboard', () => {
    renderWithMode('dark');
    expect(
      screen.getByRole('link', { name: 'Back to dashboard' }),
    ).toHaveAttribute('href', '/app/dashboard');
  });

  it.each(['light', 'dark'] as const)(
    'shows the 404 illustration in %s mode',
    (mode) => {
      const { container } = renderWithMode(mode);
      expect(container.querySelector('img')).toHaveAttribute(
        'src',
        '/static/images/astronaut.png',
      );
    },
  );
});
