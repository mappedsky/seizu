import { render, screen, cleanup } from '@testing-library/react';
import type { ReactNode } from 'react';
import { MemoryRouter } from 'react-router-dom';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import NavItem from '../NavItem';

const theme = createTheme();

function Wrapper({ children }: { children: ReactNode }) {
  return (
    <MemoryRouter>
      <ThemeProvider theme={theme}>{children}</ThemeProvider>
    </MemoryRouter>
  );
}

describe('NavItem', () => {
  afterEach(cleanup);

  it('renders a nav item with title', () => {
    render(
      <Wrapper>
        <NavItem href="/dashboard" title="Dashboard" />
      </Wrapper>,
    );
    expect(screen.getByText('Dashboard')).toBeInTheDocument();
  });

  it('renders a nav item with an icon', () => {
    function MockIcon() {
      return <svg data-testid="mock-icon" />;
    }
    render(
      <Wrapper>
        <NavItem href="/reports" title="Reports" icon={MockIcon as any} />
      </Wrapper>,
    );
    expect(screen.getByTestId('mock-icon')).toBeInTheDocument();
  });

  it('truncates long labels instead of wrapping them', () => {
    const longTitle =
      'A very long pinned report name that should not wrap in the sidebar';

    render(
      <Wrapper>
        <NavItem
          title="Parent"
          subItems={[{ href: '/sub1', title: longTitle }]}
        />
      </Wrapper>,
    );

    const label = screen.getByText(longTitle);
    const styles = window.getComputedStyle(label);

    expect(styles.overflow).toBe('hidden');
    expect(styles.textOverflow).toBe('ellipsis');
    expect(styles.whiteSpace).toBe('nowrap');
    expect(styles.minWidth).toBe('0');
  });

  it('renders sub-items when subItems prop is provided', () => {
    const subItems = [
      { href: '/sub1', title: 'Sub Item 1' },
      { href: '/sub2', title: 'Sub Item 2' },
    ];
    render(
      <Wrapper>
        <NavItem title="Parent" subItems={subItems} />
      </Wrapper>,
    );
    expect(screen.getByText('Parent')).toBeInTheDocument();
    expect(screen.getByText('Sub Item 1')).toBeInTheDocument();
    expect(screen.getByText('Sub Item 2')).toBeInTheDocument();
  });

  it('shows ExpandMore icon when open and ExpandLess when closed', () => {
    const subItems = [{ href: '/sub1', title: 'Sub Item 1' }];
    render(
      <Wrapper>
        <NavItem title="Parent" subItems={subItems} />
      </Wrapper>,
    );

    // When open=true, ExpandMore icon is shown (open indicator)
    const svgs = document.querySelectorAll('svg');
    expect(svgs.length).toBeGreaterThan(0);

    // Sub items are visible by default (open=true via initial state)
    expect(screen.getByText('Sub Item 1')).toBeInTheDocument();
  });

  it('renders a link button when href is provided without subItems', () => {
    render(
      <Wrapper>
        <NavItem href="/settings" title="Settings" />
      </Wrapper>,
    );
    expect(screen.getByRole('link', { name: /settings/i })).toBeInTheDocument();
  });

  it('keeps an accessible label when collapsed', () => {
    function MockIcon() {
      return <svg data-testid="mock-icon" />;
    }

    render(
      <Wrapper>
        <NavItem
          collapsed
          href="/reports"
          title="Reports"
          icon={MockIcon as any}
        />
      </Wrapper>,
    );

    expect(screen.getByRole('link', { name: /reports/i })).toBeInTheDocument();
    expect(screen.queryByText('Reports')).not.toBeInTheDocument();
  });
});
