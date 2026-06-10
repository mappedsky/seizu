import {
  render,
  screen,
  fireEvent,
  act,
  cleanup,
} from '@testing-library/react';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import QueryConsoleHistoryPanel from 'src/components/QueryConsoleHistoryPanel';
import * as useQueryHistoryModule from 'src/hooks/useQueryHistory';

const theme = createTheme();

function Wrapper({ children }: { children: React.ReactNode }) {
  return <ThemeProvider theme={theme}>{children}</ThemeProvider>;
}

const ITEMS = [
  {
    history_id: '1',
    user_id: 'u1',
    query: 'MATCH (n) RETURN n',
    executed_at: '2024-01-01T12:00:00Z',
  },
  {
    history_id: '2',
    user_id: 'u1',
    query: 'MATCH (a)-[r]->(b) RETURN a, r, b',
    executed_at: '2024-01-02T08:30:00Z',
  },
];

function mockHook(
  overrides: Partial<ReturnType<typeof useQueryHistoryModule.useQueryHistory>>,
) {
  const fetchHistory = jest.fn();
  jest.spyOn(useQueryHistoryModule, 'useQueryHistory').mockReturnValue({
    loading: false,
    error: null,
    data: null,
    authReady: true,
    fetchHistory,
    ...overrides,
  });
  return fetchHistory;
}

describe('QueryConsoleHistoryPanel', () => {
  afterEach(() => {
    cleanup();
    jest.restoreAllMocks();
  });

  it('shows a spinner while loading', () => {
    mockHook({ loading: true });
    render(
      <Wrapper>
        <QueryConsoleHistoryPanel onQuerySelect={jest.fn()} />
      </Wrapper>,
    );
    // CircularProgress renders an svg role="progressbar"
    expect(screen.getByRole('progressbar')).toBeInTheDocument();
  });

  it('shows an error message on failure', () => {
    mockHook({ error: new Error('fetch failed') });
    render(
      <Wrapper>
        <QueryConsoleHistoryPanel onQuerySelect={jest.fn()} />
      </Wrapper>,
    );
    expect(screen.getByText('Failed to load history')).toBeInTheDocument();
  });

  it('shows empty state when no items', () => {
    mockHook({ data: { items: [], total: 0, page: 1, per_page: 20 } });
    render(
      <Wrapper>
        <QueryConsoleHistoryPanel onQuerySelect={jest.fn()} />
      </Wrapper>,
    );
    expect(screen.getByText(/No history yet/)).toBeInTheDocument();
  });

  it('renders history items', () => {
    mockHook({ data: { items: ITEMS, total: 2, page: 1, per_page: 20 } });
    render(
      <Wrapper>
        <QueryConsoleHistoryPanel onQuerySelect={jest.fn()} />
      </Wrapper>,
    );
    expect(screen.getByText('MATCH (n) RETURN n')).toBeInTheDocument();
    expect(
      screen.getByText('MATCH (a)-[r]->(b) RETURN a, r, b'),
    ).toBeInTheDocument();
  });

  it('calls onQuerySelect with the full history item when an item is clicked', () => {
    const onQuerySelect = jest.fn();
    mockHook({ data: { items: ITEMS, total: 2, page: 1, per_page: 20 } });
    render(
      <Wrapper>
        <QueryConsoleHistoryPanel onQuerySelect={onQuerySelect} />
      </Wrapper>,
    );
    fireEvent.click(screen.getByText('MATCH (n) RETURN n'));
    expect(onQuerySelect).toHaveBeenCalledWith(ITEMS[0]);
  });

  it('does not show pagination when only one page of results', () => {
    mockHook({ data: { items: ITEMS, total: 2, page: 1, per_page: 20 } });
    const { container } = render(
      <Wrapper>
        <QueryConsoleHistoryPanel onQuerySelect={jest.fn()} />
      </Wrapper>,
    );
    // MUI Pagination nav element only renders when totalPages > 1
    expect(container.querySelector('nav')).toBeNull();
  });

  it('shows pagination when total exceeds per_page', () => {
    mockHook({ data: { items: ITEMS, total: 50, page: 1, per_page: 20 } });
    const { container } = render(
      <Wrapper>
        <QueryConsoleHistoryPanel onQuerySelect={jest.fn()} />
      </Wrapper>,
    );
    expect(container.querySelector('nav')).not.toBeNull();
  });

  it('calls fetchHistory on mount', () => {
    const fetchHistory = mockHook({});
    render(
      <Wrapper>
        <QueryConsoleHistoryPanel onQuerySelect={jest.fn()} />
      </Wrapper>,
    );
    expect(fetchHistory).toHaveBeenCalledWith(1, 20);
  });

  it('calls fetchHistory from page 1 when refreshTrigger increments', () => {
    const fetchHistory = mockHook({});
    const { rerender } = render(
      <Wrapper>
        <QueryConsoleHistoryPanel
          onQuerySelect={jest.fn()}
          refreshTrigger={0}
        />
      </Wrapper>,
    );
    fetchHistory.mockClear();

    act(() => {
      rerender(
        <Wrapper>
          <QueryConsoleHistoryPanel
            onQuerySelect={jest.fn()}
            refreshTrigger={1}
          />
        </Wrapper>,
      );
    });
    expect(fetchHistory).toHaveBeenCalledWith(1, 20);
  });

  it('does not re-fetch when refreshTrigger is 0 (initial mount)', () => {
    const fetchHistory = mockHook({});
    render(
      <Wrapper>
        <QueryConsoleHistoryPanel
          onQuerySelect={jest.fn()}
          refreshTrigger={0}
        />
      </Wrapper>,
    );
    // Only the mount fetch should have fired (page=1), not a second one for refreshTrigger=0
    expect(fetchHistory).toHaveBeenCalledTimes(1);
  });
});
