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
import { AuthConfigContext } from 'src/authConfig.context';
import { FeaturesContext } from 'src/features.context';
import * as usePermissionsModule from 'src/hooks/usePermissions';
import * as generateQueryApi from 'src/hooks/useGenerateQuery';
import type { QueryConsoleModelOptions } from 'src/hooks/useGenerateQuery';

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

  it.each([
    ['Ctrl+Enter', { ctrlKey: true }],
    ['Cmd+Enter', { metaKey: true }],
  ])('runs the query on %s and keeps Enter for a new line', (_, modifier) => {
    mockUsePermissionState.mockReturnValue({
      hasPermission: (permission: string) => permission === 'query:execute',
      loading: false,
      currentUser: null,
    });

    render(<QueryConsole />, { wrapper: Wrapper });

    const editor = screen.getByRole('textbox', { name: 'Cypher query' });
    fireEvent.change(editor, { target: { value: 'MATCH (n) RETURN n' } });
    fireEvent.keyDown(editor, { key: 'Enter' });
    expect(latestGraphProps).toBeNull();

    fireEvent.keyDown(editor, { key: 'Enter', ...modifier });
    expect(latestGraphProps?.cypher).toBe('MATCH (n) RETURN n');
  });

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

    fireEvent.change(screen.getByRole('textbox', { name: 'Cypher query' }), {
      target: { value: 'MATCH (n) RETURN n' },
    });
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

    fireEvent.change(screen.getByRole('textbox', { name: 'Cypher query' }), {
      target: { value: 'MATCH (n) RETURN n' },
    });
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
      screen.getByRole('textbox', { name: 'Cypher query' }),
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

describe('QueryConsole query generation', () => {
  let fetchSpy: jest.SpyInstance;
  let profilesSpy: jest.SpyInstance;
  let generateResponder: (body: Record<string, unknown>) => Promise<Response>;
  let consoleModels: QueryConsoleModelOptions;

  function json(body: unknown, status = 200) {
    return Promise.resolve(
      new Response(JSON.stringify(body), {
        status,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
  }

  function generated(overrides: Record<string, unknown> = {}) {
    return {
      query: 'MATCH (a:AWSAccount) RETURN a LIMIT 25',
      explanation: 'Every AWS account.',
      errors: [],
      warnings: [],
      history_id: 'h-gen',
      model_id: 'openai/fast',
      reasoning_effort: 'low',
      profile_name: 'Fast',
      ...overrides,
    };
  }

  function generateCalls() {
    return fetchSpy.mock.calls.filter(
      ([url]) => url === '/api/v1/query/generate',
    );
  }

  function renderConsole({
    feature = true,
    permissions = ['query:execute', 'chat:use'],
  }: { feature?: boolean; permissions?: string[] } = {}) {
    mockUsePermissionState.mockReturnValue({
      hasPermission: (permission: string) => permissions.includes(permission),
      loading: false,
      currentUser: null,
    });
    return render(
      <AuthConfigContext
        value={{ auth_required: false, oidc: null, loaded: true }}
      >
        <FeaturesContext
          value={{
            chat: true,
            chat_schedules: false,
            chat_connections: false,
            query_generation: feature,
          }}
        >
          <QueryConsole />
        </FeaturesContext>
      </AuthConfigContext>,
      { wrapper: Wrapper },
    );
  }

  function ask(question: string) {
    fireEvent.click(screen.getByRole('button', { name: 'Ask' }));
    const input = screen.getByRole('textbox', { name: 'Question' });
    fireEvent.change(input, { target: { value: question } });
    fireEvent.keyDown(input, { key: 'Enter' });
  }

  function editorValue() {
    return (
      screen.getByRole('textbox', {
        name: 'Cypher query',
      }) as HTMLTextAreaElement
    ).value;
  }

  beforeEach(() => {
    jest.clearAllMocks();
    window.localStorage.clear();
    graphRenders.length = 0;
    latestGraphProps = null;
    consoleModels = { available: true, profiles: [], default_profile_id: null };
    generateResponder = () => json(generated());
    // Stubbed rather than fetched: another suite replaces this module for the
    // whole process, so the real hook is not reliably the one that runs.
    profilesSpy = jest
      .spyOn(generateQueryApi, 'useQueryConsoleModels')
      .mockImplementation(() => ({ options: consoleModels, loading: false }));
    fetchSpy = jest.spyOn(global, 'fetch').mockImplementation((input, init) => {
      const url = String(input);
      if (url === '/api/v1/query/generate') {
        return generateResponder(JSON.parse(String(init?.body)));
      }
      return json({}, 404);
    });
  });

  afterEach(() => {
    fetchSpy.mockRestore();
    profilesSpy.mockRestore();
    cleanup();
  });

  it('offers no Ask mode when the feature is off', () => {
    renderConsole({ feature: false });
    expect(screen.queryByRole('button', { name: 'Ask' })).toBeNull();
    expect(screen.getByRole('textbox', { name: 'Cypher query' })).toBeTruthy();
  });

  it('offers no Ask mode without chat:use', () => {
    renderConsole({ permissions: ['query:execute'] });
    expect(screen.queryByRole('button', { name: 'Ask' })).toBeNull();
  });

  it('runs a validated query by its history entry and keeps the question above it', async () => {
    renderConsole();

    ask('list my aws accounts');

    await waitFor(() => expect(latestGraphProps?.queryHistoryId).toBe('h-gen'));
    expect(latestGraphProps?.cypher).toBeUndefined();
    expect(JSON.parse(generateCalls()[0][1].body)).toEqual({
      question: 'list my aws accounts',
      current_query: '',
    });
    expect(editorValue()).toBe('MATCH (a:AWSAccount) RETURN a LIMIT 25');
    const question = screen.getByTestId('query-question');
    expect(question.textContent).toContain('list my aws accounts');
    expect(question.textContent).toContain('Every AWS account.');
    expect(question.textContent).toContain('Fast · low');
    expect(
      screen
        .getByRole('button', { name: 'Cypher' })
        .getAttribute('aria-pressed'),
    ).toBe('true');
  });

  it('refines the generated query from its question', async () => {
    renderConsole();
    ask('list my aws accounts');
    await screen.findByRole('button', { name: 'Refine' });

    fireEvent.click(screen.getByRole('button', { name: 'Refine' }));

    const input = screen.getByRole('textbox', {
      name: 'Question',
    }) as HTMLTextAreaElement;
    // The old question is context, not something to send again.
    expect(input.value).toBe('');
    expect(screen.getByTestId('query-question').textContent).toContain(
      'Refining “list my aws accounts”',
    );
    fireEvent.change(input, { target: { value: 'only production' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(generateCalls()).toHaveLength(2));
    expect(JSON.parse(generateCalls()[1][1].body).current_query).toBe(
      'MATCH (a:AWSAccount) RETURN a LIMIT 25',
    );
  });

  it('leaves an empty Ask field with Escape', () => {
    renderConsole();
    fireEvent.click(screen.getByRole('button', { name: 'Ask' }));

    fireEvent.keyDown(screen.getByRole('textbox', { name: 'Question' }), {
      key: 'Escape',
    });

    expect(screen.queryByRole('textbox', { name: 'Question' })).toBeNull();
    expect(screen.getByRole('textbox', { name: 'Cypher query' })).toBeTruthy();
  });

  it('drops the question once a hand-edited query runs', async () => {
    renderConsole();
    ask('everything');
    await screen.findByTestId('query-question');

    const editor = screen.getByRole('textbox', { name: 'Cypher query' });
    fireEvent.change(editor, {
      target: { value: 'MATCH (n) RETURN n LIMIT 5' },
    });
    fireEvent.keyDown(editor, { key: 'Enter', ctrlKey: true });

    expect(latestGraphProps?.cypher).toBe('MATCH (n) RETURN n LIMIT 5');
    expect(screen.queryByTestId('query-question')).toBeNull();
  });

  it('puts an invalid query in the editor without running it', async () => {
    generateResponder = () =>
      json(
        generated({
          query: 'MATCH (n RETURN n',
          errors: ['Invalid input'],
          history_id: null,
        }),
      );
    renderConsole();

    ask('broken please');

    await waitFor(() =>
      expect(screen.getByTestId('query-question').textContent).toContain(
        'Invalid input',
      ),
    );
    expect(screen.getByTestId('query-question').textContent).toContain(
      'did not validate',
    );
    expect(latestGraphProps).toBeNull();
    expect(editorValue()).toBe('MATCH (n RETURN n');
  });

  it('keeps the question and the mode when generation fails', async () => {
    generateResponder = () => json({ error: 'Query generation failed' }, 502);
    renderConsole();

    ask('anything');

    await waitFor(() =>
      expect(screen.getByTestId('query-question').textContent).toContain(
        'could not be generated',
      ),
    );
    expect(
      (screen.getByRole('textbox', { name: 'Question' }) as HTMLTextAreaElement)
        .value,
    ).toBe('anything');
    expect(latestGraphProps).toBeNull();
  });

  it('shows why a chosen model profile cannot be used', async () => {
    generateResponder = () =>
      json({ error: 'The selected model profile is no longer available' }, 503);
    renderConsole();

    ask('anything');

    await waitFor(() =>
      expect(screen.getByTestId('query-question').textContent).toContain(
        'no longer available',
      ),
    );
  });

  it('cancels a generation and ignores its late answer', async () => {
    let finish: (response: Response) => void = () => undefined;
    let aborted = false;
    generateResponder = () =>
      new Promise<Response>((resolve) => {
        finish = resolve;
      });
    fetchSpy.mockImplementation((_input, init) => {
      init?.signal?.addEventListener('abort', () => {
        aborted = true;
      });
      return generateResponder({});
    });
    renderConsole();

    ask('slow question');
    expect(await screen.findByText(/Generating · \d+s/)).toBeTruthy();
    expect(screen.getByTestId('query-composer').getAttribute('aria-busy')).toBe(
      'true',
    );

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(aborted).toBe(true);
    await act(async () => {
      finish(
        new Response(JSON.stringify(generated()), {
          headers: { 'Content-Type': 'application/json' },
        }),
      );
    });

    expect(latestGraphProps).toBeNull();
    expect(screen.queryByText(/Generating/)).toBeNull();
    expect(
      (screen.getByRole('textbox', { name: 'Question' }) as HTMLTextAreaElement)
        .value,
    ).toBe('slow question');
  });

  function fastProfile(
    efforts: QueryConsoleModelOptions['profiles'][number]['reasoning_efforts'],
  ) {
    return {
      profile_id: 'p-fast',
      name: 'Fast',
      description: '',
      model_id: 'openai/fast',
      default_reasoning_effort: efforts[0],
      reasoning_efforts: efforts,
    };
  }

  it('sends the picked model profile and reasoning level', async () => {
    consoleModels = {
      available: true,
      profiles: [fastProfile(['low', 'high'])],
      default_profile_id: 'p-fast',
    };
    renderConsole();
    fireEvent.click(screen.getByRole('button', { name: 'Ask' }));
    await screen.findByText('Fast · low');

    const input = screen.getByRole('textbox', { name: 'Question' });
    fireEvent.change(input, { target: { value: 'anything' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    await waitFor(() => expect(generateCalls()).toHaveLength(1));
    expect(JSON.parse(generateCalls()[0][1].body)).toMatchObject({
      model_profile_id: 'p-fast',
      reasoning_effort: 'low',
    });
  });

  it('hides the picker when there is only one model and level', async () => {
    consoleModels = {
      available: true,
      profiles: [fastProfile(['low'])],
      default_profile_id: 'p-fast',
    };
    renderConsole();
    fireEvent.click(screen.getByRole('button', { name: 'Ask' }));

    expect(
      screen.queryByRole('combobox', { name: 'Model and reasoning' }),
    ).toBeNull();
    const input = screen.getByRole('textbox', { name: 'Question' });
    fireEvent.change(input, { target: { value: 'anything' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    // The one choice is still sent, so the server runs what the console offers.
    await waitFor(() => expect(generateCalls()).toHaveLength(1));
    expect(JSON.parse(generateCalls()[0][1].body)).toMatchObject({
      model_profile_id: 'p-fast',
      reasoning_effort: 'low',
    });
  });

  it('offers no Ask mode when no profile enables the console', () => {
    window.localStorage.setItem('seizu:query-console:composer-mode', 'ask');
    consoleModels = {
      available: false,
      profiles: [],
      default_profile_id: null,
    };
    renderConsole();

    expect(screen.queryByRole('button', { name: 'Ask' })).toBeNull();
    expect(screen.getByRole('textbox', { name: 'Cypher query' })).toBeTruthy();
  });

  it('remembers Ask mode across visits', () => {
    const first = renderConsole();
    fireEvent.click(screen.getByRole('button', { name: 'Ask' }));
    first.unmount();

    renderConsole();

    expect(screen.getByRole('textbox', { name: 'Question' })).toBeTruthy();
  });
});
