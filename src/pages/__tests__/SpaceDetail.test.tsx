import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import SpaceDetail from 'src/pages/SpaceDetail';
import * as spacesApiModule from 'src/hooks/useSpacesApi';
import * as reportsApiModule from 'src/hooks/useReportsApi';
import * as permissionsModule from 'src/hooks/usePermissions';

jest.mock('src/hooks/usePermissions', () => ({
  usePermissions: jest.fn(),
}));

// The report body is exercised by ReportPane.test.tsx; here we only care which
// report is handed to it, and that each one gets a fresh instance. The mock
// records every mount so a missing `key` (which would reuse the instance
// across a report switch) is detectable.
jest.mock('src/components/ReportPane', () => {
  const react = require('react');
  return {
    __esModule: true,
    default: ({ id }: { id: string | undefined }) => {
      react.useEffect(() => {
        const g = globalThis as { __paneMounts?: string[] };
        g.__paneMounts = [...(g.__paneMounts ?? []), id ?? ''];
      }, []);
      return react.createElement('div', { 'data-testid': 'report-pane' }, id);
    },
  };
});

function paneMounts(): string[] {
  return (globalThis as { __paneMounts?: string[] }).__paneMounts ?? [];
}

const mockUsePermissions =
  permissionsModule.usePermissions as jest.MockedFunction<
    typeof permissionsModule.usePermissions
  >;
const theme = createTheme();

function report(
  reportId: string,
  name: string,
  subspaceId: string | null = null,
) {
  return {
    report_id: reportId,
    name,
    description: '',
    current_version: 1,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    created_by: 'alice',
    updated_by: 'alice',
    access: { scope: 'public' as const },
    pinned: false,
    space_id: 'sp1',
    subspace_id: subspaceId,
  };
}

const TREE = {
  space: {
    space_id: 'sp1',
    name: 'Cloud Security',
    description: '',
    overview_report_id: 'r1',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    created_by: 'alice',
    updated_by: 'alice',
  },
  subspaces: [
    {
      subspace_id: 'ss1',
      space_id: 'sp1',
      name: 'Network',
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
      created_by: 'alice',
      updated_by: 'alice',
    },
    {
      subspace_id: 'ss2',
      space_id: 'sp1',
      name: 'Empty Group',
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
      created_by: 'alice',
      updated_by: 'alice',
    },
  ],
  reports: [
    report('r1', 'Loose Report'),
    report('r2', 'Grouped Report', 'ss1'),
  ],
};

function LocationTracker() {
  const location = useLocation();
  return <div data-testid="location">{location.pathname}</div>;
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <ThemeProvider theme={theme}>
        <Routes>
          <Route
            path="/app/spaces/:spaceId"
            element={
              <>
                <SpaceDetail />
                <LocationTracker />
              </>
            }
          />
          <Route
            path="/app/spaces/:spaceId/reports/:reportId"
            element={
              <>
                <SpaceDetail />
                <LocationTracker />
              </>
            }
          />
        </Routes>
      </ThemeProvider>
    </MemoryRouter>,
  );
}

describe('SpaceDetail', () => {
  let mockUseReportsMutations: jest.Mock;
  let mockUseSpaceTree: jest.Mock;
  let mockUseSubspaceMutations: jest.Mock;
  let mockUseSpaceMutations: jest.Mock;
  let setSpaceOverview: jest.Mock;

  beforeEach(() => {
    jest.clearAllMocks();
    // spyOn, not jest.mock: module-factory mocks are process-wide in Bun, so a
    // factory here would leak into every other suite that needs the real hooks
    // (SpaceDetailFetch.test.tsx exercises the actual fetch path).
    mockUseReportsMutations = jest.spyOn(
      reportsApiModule,
      'useReportsMutations',
    ) as unknown as jest.Mock;
    mockUseSpaceTree = jest.spyOn(
      spacesApiModule,
      'useSpaceTree',
    ) as unknown as jest.Mock;
    mockUseSubspaceMutations = jest.spyOn(
      spacesApiModule,
      'useSubspaceMutations',
    ) as unknown as jest.Mock;
    mockUseSpaceMutations = jest.spyOn(
      spacesApiModule,
      'useSpaceMutations',
    ) as unknown as jest.Mock;
    mockUsePermissions.mockReturnValue(() => true);
    mockUseSpaceTree.mockReturnValue({
      tree: TREE,
      loading: false,
      error: null,
      refresh: jest.fn(),
    });
    mockUseSubspaceMutations.mockReturnValue({
      createSubspace: jest.fn(),
      updateSubspace: jest.fn(),
      deleteSubspace: jest.fn(),
    });
    setSpaceOverview = jest.fn().mockResolvedValue(undefined);
    mockUseSpaceMutations.mockReturnValue({
      createSpace: jest.fn(),
      updateSpace: jest.fn(),
      deleteSpace: jest.fn(),
      setSpaceOverview,
    });
    mockUseReportsMutations.mockReturnValue({
      createReport: jest.fn(),
      setReportSpace: jest.fn().mockResolvedValue(undefined),
    });
  });

  afterEach(() => {
    cleanup();
    mockUseReportsMutations.mockRestore?.();
    mockUseSpaceTree.mockRestore?.();
    mockUseSubspaceMutations.mockRestore?.();
    mockUseSpaceMutations.mockRestore?.();
  });

  it('renders the pinned overview report by default', () => {
    renderAt('/app/spaces/sp1');

    expect(screen.getByTestId('report-pane')).toHaveTextContent('r1');
  });

  it('renders the selected member report', () => {
    renderAt('/app/spaces/sp1/reports/r2');

    expect(screen.getByTestId('report-pane')).toHaveTextContent('r2');
  });

  it('lists ungrouped reports before sub-space groups', () => {
    renderAt('/app/spaces/sp1');

    const items = screen
      .getAllByRole('button')
      .map((node) => node.textContent ?? '')
      .filter((text) => ['Loose Report', 'Grouped Report'].includes(text));
    expect(items).toEqual(['Loose Report', 'Grouped Report']);
    // The pinned report is an ordinary member row, not a separate entry.
    expect(
      screen.getByRole('button', { name: /Loose Report/ }),
    ).toBeInTheDocument();
  });

  it('renders a sub-space heading even when it has no reports', () => {
    renderAt('/app/spaces/sp1');

    expect(screen.getByText('Empty Group')).toBeInTheDocument();
    expect(screen.getByText('No reports')).toBeInTheDocument();
  });

  it('treats a report with an unresolvable sub-space as ungrouped', () => {
    mockUseSpaceTree.mockReturnValue({
      tree: {
        ...TREE,
        subspaces: [],
        // The API normalises dangling ids to null before they reach the client.
        reports: [report('r1', 'Loose Report'), report('r3', 'Orphan')],
      },
      loading: false,
      error: null,
      refresh: jest.fn(),
    });

    renderAt('/app/spaces/sp1');

    expect(screen.getByRole('button', { name: 'Orphan' })).toBeInTheDocument();
  });

  it('navigates when a member report is selected', () => {
    renderAt('/app/spaces/sp1');

    fireEvent.click(screen.getByRole('button', { name: 'Grouped Report' }));

    expect(screen.getByTestId('location')).toHaveTextContent(
      '/app/spaces/sp1/reports/r2',
    );
  });

  it('shows an explicit state for a report that is not in the space', () => {
    renderAt('/app/spaces/sp1/reports/not-here');

    expect(screen.getByText('Report not in this space')).toBeInTheDocument();
    // No silent redirect to the overview.
    expect(screen.queryByTestId('report-pane')).toBeNull();
    expect(screen.getByTestId('location')).toHaveTextContent(
      '/app/spaces/sp1/reports/not-here',
    );
  });

  it('shows an error state when the space fails to load', () => {
    mockUseSpaceTree.mockReturnValue({
      tree: null,
      loading: false,
      error: new Error('boom'),
      refresh: jest.fn(),
    });

    renderAt('/app/spaces/sp1');

    expect(screen.getByText('Failed to load space')).toBeInTheDocument();
  });

  it('offers an All reports link when the space has member reports', () => {
    renderAt('/app/spaces/sp1');

    expect(
      screen.getByRole('button', { name: 'All reports' }),
    ).toBeInTheDocument();
    expect(screen.queryByText('No reports yet')).toBeNull();
  });

  it('calls out how to add reports when the space has none', () => {
    mockUseSpaceTree.mockReturnValue({
      tree: {
        ...TREE,
        // The API blanks a pointer that no longer resolves.
        space: { ...TREE.space, overview_report_id: null },
        subspaces: [],
        reports: [],
      },
      loading: false,
      error: null,
      refresh: jest.fn(),
    });

    renderAt('/app/spaces/sp1');

    expect(screen.getByText('No reports yet')).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /go to reports/i }),
    ).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'All reports' })).toBeNull();
  });

  it('still calls out adding reports when only empty sub-spaces exist', () => {
    mockUseSpaceTree.mockReturnValue({
      tree: {
        ...TREE,
        space: { ...TREE.space, overview_report_id: null },
        reports: [],
      },
      loading: false,
      error: null,
      refresh: jest.fn(),
    });

    renderAt('/app/spaces/sp1');

    expect(screen.getByText('No reports yet')).toBeInTheDocument();
  });

  it('keeps the panel header and footer outside the scroll region', () => {
    // The regression this guards: with the whole panel scrollable (and the page
    // unbounded in height), the "Reports" header scrolled up under the fixed
    // navbar and clipped the space entry below it.
    renderAt('/app/spaces/sp1');

    const scroll = screen.getByTestId('space-reports-scroll');
    expect(scroll).toContainElement(
      screen.getByRole('button', { name: /Loose Report/ }),
    );
    expect(scroll).not.toContainElement(screen.getByText('Space'));
    expect(scroll).not.toContainElement(
      screen.getByRole('button', { name: 'New sub-space' }),
    );
    expect(scroll).not.toContainElement(
      screen.getByRole('button', { name: 'All reports' }),
    );
  });

  // -------------------------------------------------------------------------
  // Report-pane isolation and permission gating
  // -------------------------------------------------------------------------

  it('remounts the report pane when the selected report changes', () => {
    // ReportPane holds displayed-report and edit state, and EditableReportView
    // seeds its editor once on mount. Without a fresh instance per report,
    // saving after A -> B could write A's editor contents against B's id.
    (globalThis as { __paneMounts?: string[] }).__paneMounts = [];

    renderAt('/app/spaces/sp1');
    expect(paneMounts()).toEqual(['r1']);

    // Navigate within the same mounted tree — this is the case a shared
    // instance would silently survive.
    fireEvent.click(screen.getByRole('button', { name: 'Grouped Report' }));
    expect(screen.getByTestId('report-pane')).toHaveTextContent('r2');
    expect(paneMounts()).toEqual(['r1', 'r2']);
  });

  it('gates report actions on reports:write, not spaces:write', () => {
    // A custom role with spaces:write but not reports:write must not be shown
    // move/remove actions the API will refuse.
    mockUsePermissions.mockReturnValue(
      (permission: string) => permission !== 'reports:write',
    );

    renderAt('/app/spaces/sp1');

    // Sub-space creation still offered (spaces:write held)...
    expect(
      screen.getByRole('button', { name: 'New sub-space' }),
    ).toBeInTheDocument();
    // ...but no per-report menu.
    expect(screen.queryByLabelText('Report actions')).toBeNull();
  });

  it('gates sub-space creation on spaces:write, not reports:write', () => {
    mockUsePermissions.mockReturnValue(
      (permission: string) => permission !== 'spaces:write',
    );

    renderAt('/app/spaces/sp1');

    expect(screen.queryByRole('button', { name: 'New sub-space' })).toBeNull();
    // Report actions remain, since reports:write is held.
    expect(screen.getAllByLabelText('Report actions').length).toBeGreaterThan(
      0,
    );
  });

  // -------------------------------------------------------------------------
  // Overview pointer
  // -------------------------------------------------------------------------

  it('marks the pinned report and offers to clear it', async () => {
    const user = userEvent.setup({ delay: null });
    renderAt('/app/spaces/sp1');

    // r1 is pinned, so its row menu offers to clear rather than set.
    await user.click(screen.getAllByLabelText('Report actions')[0]);
    await user.click(
      screen.getByRole('menuitem', { name: 'Clear space overview' }),
    );

    expect(setSpaceOverview).toHaveBeenCalledWith('sp1', null);
  });

  it('sets an unpinned report as the overview', async () => {
    const user = userEvent.setup({ delay: null });
    renderAt('/app/spaces/sp1');

    // r2 is not pinned.
    await user.click(screen.getAllByLabelText('Report actions')[1]);
    await user.click(
      screen.getByRole('menuitem', { name: 'Set as space overview' }),
    );

    expect(setSpaceOverview).toHaveBeenCalledWith('sp1', 'r2');
  });

  it('gates the overview action on spaces:write', async () => {
    mockUsePermissions.mockReturnValue(
      (permission: string) => permission !== 'spaces:write',
    );
    const user = userEvent.setup({ delay: null });
    renderAt('/app/spaces/sp1');

    await user.click(screen.getAllByLabelText('Report actions')[0]);

    expect(
      screen.getByRole('menuitem', { name: /space overview/ }),
    ).toHaveAttribute('aria-disabled', 'true');
  });

  it('prompts to pin a report when the space has some but none pinned', () => {
    mockUseSpaceTree.mockReturnValue({
      tree: { ...TREE, space: { ...TREE.space, overview_report_id: null } },
      loading: false,
      error: null,
      refresh: jest.fn(),
    });

    renderAt('/app/spaces/sp1');

    // No report is rendered; the page names the action instead of guessing.
    expect(screen.queryByTestId('report-pane')).toBeNull();
    expect(
      screen.getByText(/Set a report as this space's overview/),
    ).toBeInTheDocument();
  });

  it('creates a report filed in the space', async () => {
    const createReport = jest
      .fn()
      .mockResolvedValue({ report_id: 'new1', name: 'Fresh' });
    mockUseReportsMutations.mockReturnValue({
      createReport,
      setReportSpace: jest.fn().mockResolvedValue(undefined),
    });
    const user = userEvent.setup({ delay: null });
    renderAt('/app/spaces/sp1');

    await user.click(screen.getByRole('button', { name: 'New report here' }));
    await user.type(
      screen.getByRole('textbox', { name: /report name/i }),
      'Fresh',
    );
    await user.click(screen.getByRole('button', { name: 'Create' }));

    // Created and filed in one call — no leave-and-come-back.
    await waitFor(() => {
      expect(createReport).toHaveBeenCalledWith('Fresh', 'sp1');
    });
  });
});
