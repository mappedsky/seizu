import { render, screen, cleanup } from '@testing-library/react';
import type { ReactNode } from 'react';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import Hidden from '../Hidden';

const theme = createTheme();

function Wrapper({ children }: { children: ReactNode }) {
  return <ThemeProvider theme={theme}>{children}</ThemeProvider>;
}

describe('Hidden', () => {
  afterEach(cleanup);

  it('renders children when not hidden', () => {
    // useMediaQuery returns false by default in jsdom (no window.matchMedia)
    // so lgUp will not match, meaning children should be visible
    render(
      <Wrapper>
        <Hidden>
          <span>visible content</span>
        </Hidden>
      </Wrapper>,
    );
    expect(screen.getByText('visible content')).toBeInTheDocument();
  });

  it('uses the default down-lg rule when lgUp is absent', () => {
    render(
      <Wrapper>
        <Hidden>
          <span>responsive content</span>
        </Hidden>
      </Wrapper>,
    );
    expect(screen.getByText('responsive content')).toBeInTheDocument();
  });
});
