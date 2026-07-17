import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import { AuthContext } from 'src/auth.context';
import { AuthConfigContext } from 'src/authConfig.context';
import EditableReportView from 'src/components/EditableReportView';
import { Report } from 'src/config.context';
import {
  CurrentUserStateProvider,
  type CurrentUser,
} from 'src/hooks/useCurrentUser';
import * as permissionsModule from 'src/hooks/usePermissions';

jest.mock('src/hooks/usePermissions', () => ({
  usePermissionState: jest.fn(),
}));

jest.mock('src/components/reports/PanelEditor', () => ({
  __esModule: true,
  default: () => null,
}));

// react-grid-layout requires layout measurement that happy-dom doesn't perform.
// Bypass the WidthProvider gate so panel cards render synchronously in tests.
jest.mock('src/components/reports/PanelGridRow', () => ({
  __esModule: true,
  default: ({
    panels,
    renderPanel,
  }: {
    panels: unknown[];
    renderPanel: (panel: unknown, idx: number) => React.ReactNode;
  }) =>
    panels.map((panel, idx) => (
      // eslint-disable-next-line @eslint-react/no-array-index-key
      <div key={idx}>{renderPanel(panel, idx)}</div>
    )),
}));

const theme = createTheme();
const mockUsePermissionState =
  permissionsModule.usePermissionState as jest.MockedFunction<
    typeof permissionsModule.usePermissionState
  >;

function Wrapper({ children }: { children: React.ReactNode }) {
  return <ThemeProvider theme={theme}>{children}</ThemeProvider>;
}

const REPORT: Report = {
  name: 'Risk Dashboard',
  queries: {
    total: 'MATCH (n) RETURN count(n) AS total',
  },
  inputs: [
    {
      input_id: 'severity',
      type: 'text',
      label: 'Severity',
    },
  ],
  rows: [
    {
      name: 'Overview',
      panels: [
        {
          type: 'count',
          cypher: 'total',
          caption: 'Total',
        },
      ],
    },
  ],
};

function userWith(permissions: string[]): CurrentUser {
  return {
    user_id: 'u1',
    sub: 'sub-1',
    iss: 'https://idp.example',
    email: null,
    display_name: 'Editor',
    created_at: '2024-01-01T00:00:00Z',
    last_login: '2024-01-01T00:00:00Z',
    archived_at: null,
    permissions,
  };
}

function renderAsUser(report: Report, permissions: string[]) {
  const currentUser = userWith(permissions);
  mockUsePermissionState.mockReturnValue({
    hasPermission: (permission) => permissions.includes(permission),
    loading: false,
    currentUser,
  });
  return render(
    <Wrapper>
      <AuthConfigContext.Provider
        value={{ auth_required: false, oidc: null, loaded: true }}
      >
        <AuthContext.Provider value={{ accessToken: null, isLoading: false }}>
          <CurrentUserStateProvider value={{ currentUser, loading: false }}>
            <EditableReportView
              report={report}
              reportId="r1"
              onSave={jest.fn()}
              onCancel={jest.fn()}
            />
          </CurrentUserStateProvider>
        </AuthContext.Provider>
      </AuthConfigContext.Provider>
    </Wrapper>,
  );
}

describe('EditableReportView', () => {
  let originalFetch: typeof global.fetch;
  beforeEach(() => {
    jest.clearAllMocks();
    originalFetch = global.fetch;
    mockUsePermissionState.mockReturnValue({
      hasPermission: () => false,
      loading: false,
      currentUser: null,
    });
  });
  afterEach(() => {
    cleanup();
    global.fetch = originalFetch;
  });

  it('renders the edit toolbar and editable rows', () => {
    render(
      <Wrapper>
        <EditableReportView
          report={REPORT}
          reportId="r1"
          onSave={jest.fn()}
          onCancel={jest.fn()}
        />
      </Wrapper>,
    );

    expect(screen.getByText('Editing report')).toBeInTheDocument();
    expect(screen.getByLabelText('Report name')).toHaveValue('Risk Dashboard');
    expect(screen.getByText('Named Queries')).toBeInTheDocument();
    expect(screen.getByText('Inputs')).toBeInTheDocument();
    expect(screen.getByLabelText('Row name')).toHaveValue('Overview');
  });

  it('shows an error instead of crashing for malformed rows', () => {
    const onCancel = jest.fn();
    const malformedReport = {
      name: 'Malformed Report',
      panels: [{ type: 'markdown', content: 'Wrong level' }],
    } as unknown as Report;

    render(
      <Wrapper>
        <EditableReportView
          report={malformedReport}
          reportId="r1"
          onSave={jest.fn()}
          onCancel={onCancel}
        />
      </Wrapper>,
    );

    expect(screen.getByRole('alert')).toHaveTextContent(
      'rows must be an array, with panels nested under each row',
    );
    fireEvent.click(screen.getByRole('button', { name: 'Exit editor' }));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it('saves the edited report name and comment', async () => {
    const onSave = jest.fn().mockResolvedValue(undefined);
    render(
      <Wrapper>
        <EditableReportView
          report={REPORT}
          reportId="r1"
          onSave={onSave}
          onCancel={jest.fn()}
        />
      </Wrapper>,
    );

    fireEvent.change(screen.getByLabelText('Report name'), {
      target: { value: 'Updated Risk Dashboard' },
    });
    fireEvent.change(screen.getByLabelText('Save comment (optional)'), {
      target: { value: 'Tighten layout' },
    });
    fireEvent.click(screen.getByRole('button', { name: /save version/i }));

    await waitFor(() =>
      expect(onSave).toHaveBeenCalledWith(
        expect.objectContaining({ name: 'Updated Risk Dashboard' }),
        'Tighten layout',
      ),
    );
  });

  it('saves a locally edited row name', async () => {
    const onSave = jest.fn().mockResolvedValue(undefined);
    render(
      <Wrapper>
        <EditableReportView
          report={REPORT}
          reportId="r1"
          onSave={onSave}
          onCancel={jest.fn()}
        />
      </Wrapper>,
    );

    fireEvent.change(screen.getByLabelText('Row name'), {
      target: { value: 'Updated Overview' },
    });
    fireEvent.blur(screen.getByLabelText('Row name'));
    fireEvent.click(screen.getByRole('button', { name: /save version/i }));

    await waitFor(() =>
      expect(onSave).toHaveBeenCalledWith(
        expect.objectContaining({
          rows: [expect.objectContaining({ name: 'Updated Overview' })],
        }),
        '',
      ),
    );
  });

  it('saves a locally edited named query value', async () => {
    const onSave = jest.fn().mockResolvedValue(undefined);
    render(
      <Wrapper>
        <EditableReportView
          report={REPORT}
          reportId="r1"
          onSave={onSave}
          onCancel={jest.fn()}
        />
      </Wrapper>,
    );

    fireEvent.change(screen.getByLabelText('Cypher'), {
      target: { value: 'MATCH (n) RETURN n LIMIT 1' },
    });
    fireEvent.blur(screen.getByLabelText('Cypher'));
    fireEvent.click(screen.getByRole('button', { name: /save version/i }));

    await waitFor(() =>
      expect(onSave).toHaveBeenCalledWith(
        expect.objectContaining({
          queries: { total: 'MATCH (n) RETURN n LIMIT 1' },
        }),
        '',
      ),
    );
  });

  it('renders a live panel preview via the token-less ad-hoc path when the user can execute queries', async () => {
    global.fetch = jest.fn().mockResolvedValue({
      json: () => Promise.resolve({ results: [{ total: 5 }] }),
    }) as unknown as typeof global.fetch;

    renderAsUser(REPORT, ['reports:write', 'query:execute']);

    // The panel's named cypher resolves against the unsaved queries map and is
    // executed via the ad-hoc endpoint (no signed capability token).
    await waitFor(() =>
      expect(global.fetch).toHaveBeenCalledWith(
        '/api/v1/query/adhoc',
        expect.anything(),
      ),
    );
    const body = JSON.parse((global.fetch as jest.Mock).mock.calls[0][1].body);
    expect(body.query).toBe('MATCH (n) RETURN count(n) AS total');
  });

  it('falls back to a skeleton (no query) when the user lacks query:execute', async () => {
    global.fetch = jest.fn() as unknown as typeof global.fetch;
    const { container } = renderAsUser(REPORT, ['reports:write']);

    await waitFor(() =>
      expect(screen.getByLabelText('Row name')).toBeInTheDocument(),
    );
    expect(container.querySelector('.MuiSkeleton-root')).not.toBeNull();
    expect(global.fetch).not.toHaveBeenCalled();
  });
});
