import {
  act,
  fireEvent,
  render,
  screen,
  cleanup,
  waitFor,
} from '@testing-library/react';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import { BrowserRouter, MemoryRouter } from 'react-router-dom';
import QueryConsole from 'src/pages/QueryConsole';
import * as usePermissionsModule from 'src/hooks/usePermissions';

const mockSchemaPanel = jest.fn(
  ({
    open,
    onToggle,
  }: {
    open: boolean;
    onToggle: (tab?: 'schema' | 'history') => void;
  }) => (
    <div>
      <div data-testid="schema-panel" data-open={String(open)} />
      <button type="button" onClick={() => onToggle()}>
        Toggle schema
      </button>
    </div>
  ),
);

jest.mock('src/hooks/usePermissions', () => ({
  usePermissionState: jest.fn(),
}));

jest.mock('src/components/QueryConsoleSchemaPanel', () => ({
  __esModule: true,
  default: (props: {
    open: boolean;
    onToggle: (tab?: 'schema' | 'history') => void;
  }) => mockSchemaPanel(props),
}));

interface GraphProps {
  cypher?: string;
  queryHistoryId?: string;
  refreshKey?: number;
  onQueryComplete?: (historyId: string | null) => void;
}

// Records what the console asked for on every render, and lets a test finish a
// query the way the real panel does.
const graphRenders: GraphProps[] = [];
let latestGraphProps: GraphProps | null = null;

jest.mock('src/components/reports/CypherGraph', () => ({
  __esModule: true,
  default: (props: GraphProps) => {
    graphRenders.push(props);
    latestGraphProps = props;
    return <div data-testid="cypher-graph" />;
  },
}));

const mockUsePermissionState =
  usePermissionsModule.usePermissionState as jest.MockedFunction<
    typeof usePermissionsModule.usePermissionState
  >;
const theme = createTheme();

function Wrapper({ children }: { children: React.ReactNode }) {
  return (
    <MemoryRouter>
      <ThemeProvider theme={theme}>{children}</ThemeProvider>
    </MemoryRouter>
  );
}

describe('QueryConsole', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    window.localStorage.clear();
    graphRenders.length = 0;
    latestGraphProps = null;
  });

  afterEach(cleanup);

  // Publishing the result's URL must not read back as someone navigating to it.
  // It did: `navigate` reaches the router a commit after the state set beside
  // it, and the console treated that window as a restore -- re-running as a
  // history query, publishing again, and never settling.
  it('does not re-run when it publishes the finished query to the URL', async () => {
    mockUsePermissionState.mockReturnValue({
      hasPermission: (permission: string) => permission === 'query:execute',
      loading: false,
      currentUser: null,
    });

    render(<QueryConsole />, { wrapper: Wrapper });

    fireEvent.change(
      screen.getByPlaceholderText(
        'Enter a Cypher query... (Ctrl+Enter to run)',
      ),
      { target: { value: 'MATCH (n) RETURN n' } },
    );
    fireEvent.click(screen.getByRole('button', { name: /run/i }));

    await waitFor(() =>
      expect(latestGraphProps?.cypher).toBe('MATCH (n) RETURN n'),
    );

    // The panel finishes and hands back the history record it created.
    graphRenders.length = 0;
    await act(async () => {
      latestGraphProps!.onQueryComplete!('history-1');
    });

    expect(latestGraphProps!.cypher).toBe('MATCH (n) RETURN n');
    expect(latestGraphProps!.queryHistoryId).toBeUndefined();
    // Nothing after publishing the URL may ask for the run as a history entry.
    expect(
      graphRenders.some((render) => render.queryHistoryId !== undefined),
    ).toBe(false);
  });

  it('runs the history entry a cold URL names', async () => {
    mockUsePermissionState.mockReturnValue({
      hasPermission: (permission: string) => permission === 'query:execute',
      loading: false,
      currentUser: null,
    });

    render(<QueryConsole />, {
      wrapper: ({ children }: { children: React.ReactNode }) => (
        <MemoryRouter initialEntries={['/app/query-console?h=history-9']}>
          <ThemeProvider theme={theme}>{children}</ThemeProvider>
        </MemoryRouter>
      ),
    });

    await waitFor(() =>
      expect(latestGraphProps?.queryHistoryId).toBe('history-9'),
    );
    expect(latestGraphProps!.cypher).toBeUndefined();
  });

  // The loop this reproduces needs a real history stack: `navigate` reaches the
  // router through its history listener, a commit after the state set beside
  // it, and MemoryRouter applies both together. Under BrowserRouter the two
  // land apart, which is the window the console used to misread as a restore.
  it('does not re-run itself after publishing the finished query to the URL', async () => {
    mockUsePermissionState.mockReturnValue({
      hasPermission: (permission: string) => permission === 'query:execute',
      loading: false,
      currentUser: null,
    });
    window.history.replaceState({}, '', '/app/query-console');

    render(<QueryConsole />, {
      wrapper: ({ children }: { children: React.ReactNode }) => (
        <BrowserRouter>
          <ThemeProvider theme={theme}>{children}</ThemeProvider>
        </BrowserRouter>
      ),
    });

    fireEvent.change(
      screen.getByPlaceholderText(
        'Enter a Cypher query... (Ctrl+Enter to run)',
      ),
      { target: { value: 'MATCH (n) RETURN n' } },
    );
    fireEvent.click(screen.getByRole('button', { name: /run/i }));
    await waitFor(() =>
      expect(latestGraphProps?.cypher).toBe('MATCH (n) RETURN n'),
    );

    // Finish the query the way the panel does, then let every pending commit
    // settle. Nothing may ask for a run again.
    graphRenders.length = 0;
    await act(async () => {
      latestGraphProps!.onQueryComplete!('history-1');
      await Promise.resolve();
    });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });

    expect(
      graphRenders.filter((entry) => entry.queryHistoryId !== undefined),
    ).toEqual([]);
    expect(latestGraphProps!.cypher).toBe('MATCH (n) RETURN n');
  });

  it('shows a spinner while permissions are loading', () => {
    mockUsePermissionState.mockReturnValue({
      hasPermission: () => false,
      loading: true,
      currentUser: null,
    });

    render(<QueryConsole />, { wrapper: Wrapper });

    expect(screen.getByRole('progressbar')).toBeInTheDocument();
    expect(
      screen.queryByText('You do not have access to the query console.'),
    ).not.toBeInTheDocument();
  });

  it('shows no-access message when loaded without query permission', () => {
    mockUsePermissionState.mockReturnValue({
      hasPermission: () => false,
      loading: false,
      currentUser: null,
    });

    render(<QueryConsole />, { wrapper: Wrapper });

    expect(
      screen.getByText('You do not have access to the query console.'),
    ).toBeInTheDocument();
  });

  it('renders the console when query permission is present', () => {
    mockUsePermissionState.mockReturnValue({
      hasPermission: (permission: string) => permission === 'query:execute',
      loading: false,
      currentUser: null,
    });

    render(<QueryConsole />, { wrapper: Wrapper });

    expect(screen.getByTestId('schema-panel')).toBeInTheDocument();
    expect(
      screen.getByPlaceholderText(
        'Enter a Cypher query... (Ctrl+Enter to run)',
      ),
    ).toBeInTheDocument();
  });

  it('restores the schema panel open state from localStorage', () => {
    window.localStorage.setItem(
      'seizu:query-console:schema-panel-open',
      'false',
    );
    mockUsePermissionState.mockReturnValue({
      hasPermission: (permission: string) => permission === 'query:execute',
      loading: false,
      currentUser: null,
    });

    render(<QueryConsole />, { wrapper: Wrapper });

    expect(screen.getByTestId('schema-panel')).toHaveAttribute(
      'data-open',
      'false',
    );
  });

  it('persists the schema panel toggle state to localStorage', async () => {
    mockUsePermissionState.mockReturnValue({
      hasPermission: (permission: string) => permission === 'query:execute',
      loading: false,
      currentUser: null,
    });

    render(<QueryConsole />, { wrapper: Wrapper });

    expect(
      window.localStorage.getItem('seizu:query-console:schema-panel-open'),
    ).toBe('true');

    fireEvent.click(screen.getByRole('button', { name: 'Toggle schema' }));

    await waitFor(() => {
      expect(
        window.localStorage.getItem('seizu:query-console:schema-panel-open'),
      ).toBe('false');
    });
  });
});
