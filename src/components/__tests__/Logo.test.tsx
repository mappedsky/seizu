import { render, screen, cleanup } from '@testing-library/react';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import Logo from '../Logo';

const renderWithMode = (mode: 'light' | 'dark') =>
  render(
    <ThemeProvider theme={createTheme({ palette: { mode } })}>
      <Logo />
    </ThemeProvider>,
  );

describe('Logo', () => {
  afterEach(cleanup);

  it('renders the black lockup in light mode (light app bar)', () => {
    renderWithMode('light');
    const img = screen.getByAltText('Seizu');
    expect(img).toBeInTheDocument();
    expect(img).toHaveAttribute(
      'src',
      '/static/images/logo-horizontal-black.svg',
    );
    expect(img).toHaveAttribute('height', '50');
  });

  it('renders the white lockup in dark mode (dark app bar)', () => {
    renderWithMode('dark');
    expect(screen.getByAltText('Seizu')).toHaveAttribute(
      'src',
      '/static/images/logo-horizontal-white.svg',
    );
  });

  it('passes additional props to the img element', () => {
    render(
      <ThemeProvider theme={createTheme()}>
        <Logo data-testid="logo-img" />
      </ThemeProvider>,
    );
    expect(screen.getByTestId('logo-img')).toBeInTheDocument();
  });
});
