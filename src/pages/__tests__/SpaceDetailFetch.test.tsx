/**
 * Guards the space tree against being refetched on every in-space navigation.
 *
 * The tree endpoint is the expensive one on the space detail page (on DynamoDB
 * it reads every report's list item to filter by space), so browsing report to
 * report inside a space must reuse the tree it already has.
 */
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import { AuthConfigContext } from 'src/authConfig.context';
import SpaceDetail from 'src/pages/SpaceDetail';
import * as permissionsModule from 'src/hooks/usePermissions';

jest.mock('src/hooks/usePermissions', () => ({
  usePermissions: jest.fn(),
  usePermissionState: jest.fn(),
}));

// Keep the report body out of it; this is only about the tree fetch.
jest.mock('src/components/ReportPane', () => ({
  __esModule: true,
  default: ({ id }: { id: string | undefined }) => (
    <div data-testid="report-pane">{id}</div>
  ),
}));

const theme = createTheme();
const mockUsePermissions =
  permissionsModule.usePermissions as jest.MockedFunction<
    typeof permissionsModule.usePermissions
  >;

const TREE = {
  space: {
    space_id: 'sp1',
    name: 'Cloud Security',
    description: '',
    overview_report_id: 'ovr1',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    created_by: 'alice',
    updated_by: 'alice',
  },
  subspaces: [],
  reports: [
    {
      report_id: 'ovr1',
      name: 'Cloud Security',
      description: '',
      current_version: 1,
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
      created_by: 'alice',
      updated_by: 'alice',
      access: { scope: 'public' as const },
      pinned: false,
      space_id: 'sp1',
      subspace_id: null,
      space_overview: true,
    },
    {
      report_id: 'r1',
      name: 'Member One',
      description: '',
      current_version: 1,
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
      created_by: 'alice',
      updated_by: 'alice',
      access: { scope: 'public' as const },
      pinned: false,
      space_id: 'sp1',
      subspace_id: null,
      space_overview: false,
    },
    {
      report_id: 'r2',
      name: 'Member Two',
      description: '',
      current_version: 1,
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
      created_by: 'alice',
      updated_by: 'alice',
      access: { scope: 'public' as const },
      pinned: false,
      space_id: 'sp1',
      subspace_id: null,
      space_overview: false,
    },
  ],
};

describe('SpaceDetail tree fetching', () => {
  let mockFetch: jest.Mock;

  beforeEach(() => {
    jest.clearAllMocks();
    mockUsePermissions.mockReturnValue(() => true);
    mockFetch = jest.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(TREE),
    });
    global.fetch = mockFetch as unknown as typeof fetch;
  });

  afterEach(cleanup);

  const treeCalls = () =>
    mockFetch.mock.calls.filter((call) =>
      String(call[0]).includes('/api/v1/spaces/sp1/tree'),
    ).length;

  function renderApp() {
    return render(
      <MemoryRouter initialEntries={['/app/spaces/sp1']}>
        <AuthConfigContext.Provider
          value={{ auth_required: false, oidc: null, loaded: true }}
        >
          <ThemeProvider theme={theme}>
            <Routes>
              <Route path="/app/spaces/:spaceId" element={<SpaceDetail />} />
              <Route
                path="/app/spaces/:spaceId/reports/:reportId"
                element={<SpaceDetail />}
              />
            </Routes>
          </ThemeProvider>
        </AuthConfigContext.Provider>
      </MemoryRouter>,
    );
  }

  it('fetches the tree once and reuses it while browsing reports in the space', async () => {
    const user = userEvent.setup({ delay: null });
    renderApp();

    await waitFor(() => expect(treeCalls()).toBe(1));
    expect(screen.getByTestId('report-pane')).toHaveTextContent('ovr1');

    await user.click(screen.getByRole('button', { name: 'Member One' }));
    expect(screen.getByTestId('report-pane')).toHaveTextContent('r1');

    await user.click(screen.getByRole('button', { name: 'Member Two' }));
    expect(screen.getByTestId('report-pane')).toHaveTextContent('r2');

    await user.click(screen.getByRole('button', { name: 'Cloud Security' }));
    expect(screen.getByTestId('report-pane')).toHaveTextContent('ovr1');

    // Three navigations, still one tree fetch.
    expect(treeCalls()).toBe(1);
  }, 15_000);

  it('refetches the tree when a report changes', async () => {
    renderApp();
    await waitFor(() => expect(treeCalls()).toBe(1));

    // Saving a report broadcasts this; the tree embeds report names, so the
    // sidebar entry would otherwise keep the old one.
    window.dispatchEvent(new Event('seizu:reports-updated'));

    await waitFor(() => expect(treeCalls()).toBe(2));
  }, 15_000);

  it('refetches the tree when a space or sub-space changes', async () => {
    renderApp();
    await waitFor(() => expect(treeCalls()).toBe(1));

    window.dispatchEvent(new Event('seizu:spaces-updated'));

    await waitFor(() => expect(treeCalls()).toBe(2));
  }, 15_000);

  it('does not fetch the tree twice for a single change', async () => {
    // Mutations broadcast their own invalidation, so the page must not also
    // call refresh() — that would double every tree fetch, and the tree is the
    // expensive request on this page.
    renderApp();
    await waitFor(() => expect(treeCalls()).toBe(1));

    window.dispatchEvent(new Event('seizu:spaces-updated'));
    await waitFor(() => expect(treeCalls()).toBe(2));

    // Give any stray second fetch a chance to land before asserting.
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(treeCalls()).toBe(2);
  }, 15_000);
});
