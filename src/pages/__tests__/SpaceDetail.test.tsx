import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { useEffect } from 'react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import SpaceDetail from 'src/pages/SpaceDetail';
import * as spacesApiModule from 'src/hooks/useSpacesApi';
import * as reportsApiModule from 'src/hooks/useReportsApi';
import * as permissionsModule from 'src/hooks/usePermissions';
import * as currentUserModule from 'src/hooks/useCurrentUser';

jest.mock('src/hooks/usePermissions', () => ({
  usePermissions: jest.fn(),
}));

// The report body is exercised by ReportPane.test.tsx; here we only care which
// report is handed to it, and that each one gets a fresh instance. This local
// component records every mount so a missing `key` (which would reuse the
// instance across a report switch) is detectable without leaking a module mock
// into other test files in Bun's shared process.
function MountTrackingReportPane({ id }: { id: string | undefined }) {
  useEffect(() => {
    const g = globalThis as { __paneMounts?: string[] };
    g.__paneMounts = [...(g.__paneMounts ?? []), id ?? ''];
  }, []);
  return <div data-testid="report-pane">{id}</div>;
}

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

// Reports outside sp1, as the add-reports drawer sees them.
const CANDIDATES = [
  ...TREE.reports,
  { ...report('u1', 'Unfiled Report'), space_id: null },
  { ...report('e1', 'Elsewhere Report'), space_id: 'sp2' },
  {
    ...report('d1', 'My Draft'),
    space_id: null,
    access: { scope: 'private' as const },
  },
  {
    ...report('d2', 'Their Draft'),
    space_id: null,
    created_by: 'bob',
    access: { scope: 'private' as const },
  },
];

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
                <SpaceDetail ReportPaneComponent={MountTrackingReportPane} />
                <LocationTracker />
              </>
            }
          />
          <Route
            path="/app/spaces/:spaceId/contents"
            element={
              <>
                <SpaceDetail
                  view="contents"
                  ReportPaneComponent={MountTrackingReportPane}
                />
                <LocationTracker />
              </>
            }
          />
          <Route
            path="/app/spaces/:spaceId/reports/:reportId"
            element={
              <>
                <SpaceDetail ReportPaneComponent={MountTrackingReportPane} />
                <LocationTracker />
              </>
            }
          />
          <Route path="/app/reports" element={<LocationTracker />} />
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
  let mockUseReportsList: jest.Mock;
  let mockUseSpacesList: jest.Mock;
  let mockUseCurrentUser: jest.Mock;

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
    // The add-reports drawer reads these; stub them so no suite inherits a
    // real fetch or another file's leftover spy.
    mockUseReportsList = jest.spyOn(
      reportsApiModule,
      'useReportsList',
    ) as unknown as jest.Mock;
    mockUseSpacesList = jest.spyOn(
      spacesApiModule,
      'useSpacesList',
    ) as unknown as jest.Mock;
    mockUseCurrentUser = jest.spyOn(
      currentUserModule,
      'useCurrentUser',
    ) as unknown as jest.Mock;
    mockUseReportsList.mockReturnValue({
      reports: CANDIDATES,
      total: CANDIDATES.length,
      page: 1,
      perPage: 500,
      loading: false,
      error: null,
      refresh: jest.fn(),
    });
    mockUseSpacesList.mockReturnValue({
      spaces: [
        TREE.space,
        { ...TREE.space, space_id: 'sp2', name: 'Identity' },
      ],
      loading: false,
      error: null,
      refresh: jest.fn(),
    });
    mockUseCurrentUser.mockReturnValue({ user_id: 'alice' });
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
    mockUseReportsList.mockRestore?.();
    mockUseSpacesList.mockRestore?.();
    mockUseCurrentUser.mockRestore?.();
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

  it('keeps the panel header and footer outside the scroll region', () => {
    // The regression this guards: with the whole panel scrollable (and the page
    // unbounded in height), the "Reports" header scrolled up under the fixed
    // navbar and clipped the space entry below it.
    renderAt('/app/spaces/sp1');

    const scroll = screen.getByTestId('space-reports-scroll');
    expect(scroll).toContainElement(
      screen.getByRole('button', { name: /Loose Report/ }),
    );
    // The space name is the header, and the actions are the footer.
    expect(scroll).not.toContainElement(screen.getByText('Cloud Security'));
    expect(scroll).not.toContainElement(
      screen.getByRole('button', { name: 'New sub-space' }),
    );
    expect(scroll).not.toContainElement(
      screen.getByRole('button', { name: 'Add reports' }),
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

  it('disables report actions without reports:write but keeps the menu', async () => {
    // The menu mixes permissions: the overview pointer is spaces:write and
    // membership is reports:write. A spaces:write-only role must still be able
    // to pin an overview, so the menu opens and each action gates itself.
    mockUsePermissions.mockReturnValue(
      (permission: string) => permission !== 'reports:write',
    );
    const user = userEvent.setup({ delay: null });

    renderAt('/app/spaces/sp1');

    // Sub-space creation still offered (spaces:write held)...
    expect(
      screen.getByRole('button', { name: 'New sub-space' }),
    ).toBeInTheDocument();
    // ...but no New report, which needs reports:write.
    expect(screen.queryByRole('button', { name: 'New report' })).toBeNull();

    await user.click(screen.getAllByLabelText('Report actions')[0]);
    expect(
      screen.getByRole('menuitem', { name: /space overview/ }),
    ).not.toHaveAttribute('aria-disabled', 'true');
    expect(
      screen.getByRole('menuitem', { name: 'Remove from space' }),
    ).toHaveAttribute('aria-disabled', 'true');
  });

  it('hides the report menu when neither permission is held', () => {
    mockUsePermissions.mockReturnValue(
      (permission: string) =>
        permission !== 'reports:write' && permission !== 'spaces:write',
    );

    renderAt('/app/spaces/sp1');

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

    // No report is rendered: the space's own page shows, and names the
    // action instead of guessing an overview.
    expect(screen.queryByTestId('report-pane')).toBeNull();
    expect(
      screen.getByRole('heading', { level: 1, name: 'Cloud Security' }),
    ).toBeInTheDocument();
    expect(screen.getByText(/No overview yet/)).toBeInTheDocument();
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

    await user.click(screen.getByRole('button', { name: 'New report' }));
    await user.type(
      screen.getByRole('textbox', { name: /report name/i }),
      'Fresh',
    );
    await user.click(screen.getByRole('button', { name: 'Create' }));

    // Created and filed in one call — no leave-and-come-back.
    await waitFor(() => {
      expect(createReport).toHaveBeenCalledWith('Fresh', 'sp1', null);
    });
  });

  // -------------------------------------------------------------------------
  // Footer actions
  // -------------------------------------------------------------------------

  it('offers the footer actions as one group', () => {
    renderAt('/app/spaces/sp1');

    for (const label of ['Add reports', 'New report', 'New sub-space']) {
      expect(screen.getByRole('button', { name: label })).toBeInTheDocument();
    }
  });

  it('drops the footer actions without write permissions', () => {
    mockUsePermissions.mockReturnValue(() => false);

    renderAt('/app/spaces/sp1');

    expect(screen.queryByRole('button', { name: 'Add reports' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'New sub-space' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'New report' })).toBeNull();
  });

  // -------------------------------------------------------------------------
  // Contents page
  // -------------------------------------------------------------------------

  it('shows the contents page at /contents even with an overview pinned', () => {
    renderAt('/app/spaces/sp1/contents');

    expect(screen.queryByTestId('report-pane')).toBeNull();
    expect(
      screen.getByRole('heading', { level: 1, name: 'Cloud Security' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('heading', { level: 2, name: 'Network' }),
    ).toBeInTheDocument();
    expect(screen.getByText('Nothing in Empty Group yet.')).toBeInTheDocument();
  });

  it('navigates to the contents page from the panel header', () => {
    renderAt('/app/spaces/sp1/reports/r2');

    fireEvent.click(
      screen.getByRole('button', { name: 'Cloud Security contents' }),
    );

    expect(screen.getByTestId('location')).toHaveTextContent(
      '/app/spaces/sp1/contents',
    );
  });

  it('invites adding reports to an empty space', () => {
    mockUseSpaceTree.mockReturnValue({
      tree: {
        ...TREE,
        space: { ...TREE.space, overview_report_id: null },
        subspaces: [],
        reports: [],
      },
      loading: false,
      error: null,
      refresh: jest.fn(),
    });

    renderAt('/app/spaces/sp1');

    expect(
      screen.getByRole('button', { name: 'Add existing reports' }),
    ).toBeInTheDocument();
  });

  // -------------------------------------------------------------------------
  // Adding existing reports
  // -------------------------------------------------------------------------

  it('lists only reports outside the space, flagging drafts and moves', async () => {
    const user = userEvent.setup({ delay: null });
    renderAt('/app/spaces/sp1');

    await user.click(screen.getByRole('button', { name: 'Add reports' }));

    const drawer = await screen.findByRole('presentation');
    expect(within(drawer).queryByText('Loose Report')).toBeNull();
    expect(within(drawer).getByText('Unfiled Report')).toBeInTheDocument();
    expect(
      within(drawer).getByText('In Identity — moves here'),
    ).toBeInTheDocument();
    expect(
      within(drawer).getByText('Draft — published when added'),
    ).toBeInTheDocument();
    // Another user's draft is shown but cannot be picked.
    expect(
      within(drawer).getByRole('button', { name: /Their Draft/ }),
    ).toHaveAttribute('aria-disabled', 'true');
  });

  it('publishes a selected draft before filing it into the chosen sub-space', async () => {
    const setReportSpace = jest.fn().mockResolvedValue(undefined);
    const updateReportVisibility = jest.fn().mockResolvedValue(undefined);
    mockUseReportsMutations.mockReturnValue({
      createReport: jest.fn(),
      setReportSpace,
      updateReportVisibility,
    });
    const user = userEvent.setup({ delay: null });
    renderAt('/app/spaces/sp1');

    await user.click(screen.getByRole('button', { name: 'Add reports' }));
    const drawer = await screen.findByRole('presentation');
    await user.click(within(drawer).getByRole('button', { name: /My Draft/ }));
    await user.click(
      within(drawer).getByRole('button', { name: /Unfiled Report/ }),
    );
    expect(
      within(drawer).getByText(/1 draft will be published/),
    ).toBeInTheDocument();

    await user.click(within(drawer).getByRole('combobox'));
    await user.click(screen.getByRole('option', { name: 'Network' }));
    await user.click(
      within(drawer).getByRole('button', { name: 'Add 2 reports' }),
    );

    await waitFor(() => {
      expect(setReportSpace).toHaveBeenCalledTimes(2);
    });
    expect(updateReportVisibility).toHaveBeenCalledTimes(1);
    expect(updateReportVisibility).toHaveBeenCalledWith('d1', 'public');
    // Per-item broadcasts are off; the batch reloads once at the end.
    expect(setReportSpace).toHaveBeenCalledWith('d1', 'sp1', 'ss1', {
      notify: false,
    });
    expect(setReportSpace).toHaveBeenCalledWith('u1', 'sp1', 'ss1', {
      notify: false,
    });
    // The draft is published before it is filed, never after.
    const draftFiling = setReportSpace.mock.calls.findIndex(
      ([id]) => id === 'd1',
    );
    expect(updateReportVisibility.mock.invocationCallOrder[0]).toBeLessThan(
      setReportSpace.mock.invocationCallOrder[draftFiling],
    );
  });

  it('reloads the report lists once per batch, after the last report', async () => {
    const setReportSpace = jest.fn().mockResolvedValue(undefined);
    mockUseReportsMutations.mockReturnValue({
      createReport: jest.fn(),
      setReportSpace,
      updateReportVisibility: jest.fn().mockResolvedValue(undefined),
    });
    const notify = jest.spyOn(reportsApiModule, 'notifyReportsUpdated');
    try {
      const user = userEvent.setup({ delay: null });
      renderAt('/app/spaces/sp1');

      await user.click(screen.getByRole('button', { name: 'Add reports' }));
      const drawer = await screen.findByRole('presentation');
      await user.click(
        within(drawer).getByRole('button', { name: /Unfiled Report/ }),
      );
      await user.click(
        within(drawer).getByRole('button', { name: /Elsewhere Report/ }),
      );
      await user.click(
        within(drawer).getByRole('button', { name: 'Add 2 reports' }),
      );

      await waitFor(() => expect(notify).toHaveBeenCalledTimes(1));
      expect(setReportSpace).toHaveBeenCalledTimes(2);
      expect(notify.mock.invocationCallOrder[0]).toBeGreaterThan(
        setReportSpace.mock.invocationCallOrder[1],
      );
    } finally {
      notify.mockRestore();
    }
  });

  it('keeps failed reports selected and says which failed', async () => {
    const setReportSpace = jest
      .fn()
      .mockRejectedValue(new Error('Report is not public'));
    mockUseReportsMutations.mockReturnValue({
      createReport: jest.fn(),
      setReportSpace,
      updateReportVisibility: jest.fn(),
    });
    const user = userEvent.setup({ delay: null });
    renderAt('/app/spaces/sp1');

    await user.click(screen.getByRole('button', { name: 'Add reports' }));
    const drawer = await screen.findByRole('presentation');
    await user.click(
      within(drawer).getByRole('button', { name: /Unfiled Report/ }),
    );
    await user.click(
      within(drawer).getByRole('button', { name: 'Add 1 report' }),
    );

    expect(
      await within(drawer).findByText('Unfiled Report: Report is not public'),
    ).toBeInTheDocument();
    expect(
      within(drawer).getByRole('button', { name: 'Add 1 report' }),
    ).toBeEnabled();
  });

  it('starts the picker on a sub-space opened from its menu', async () => {
    const user = userEvent.setup({ delay: null });
    renderAt('/app/spaces/sp1');

    await user.click(screen.getAllByLabelText('Sub-space actions')[0]);
    await user.click(
      screen.getByRole('menuitem', { name: 'Add reports here…' }),
    );

    const drawer = await screen.findByRole('presentation');
    expect(within(drawer).getByRole('combobox')).toHaveTextContent('Network');
  });

  // -------------------------------------------------------------------------
  // Moving reports between sub-spaces
  // -------------------------------------------------------------------------

  it('offers each other sub-space as a one-click move target', async () => {
    const setReportSpace = jest.fn().mockResolvedValue(undefined);
    mockUseReportsMutations.mockReturnValue({
      createReport: jest.fn(),
      setReportSpace,
    });
    const user = userEvent.setup({ delay: null });
    renderAt('/app/spaces/sp1');

    // r1 is at the top level, so the top level is not offered.
    await user.click(screen.getAllByLabelText('Report actions')[0]);
    expect(
      screen.queryByRole('menuitem', { name: 'Move to top level' }),
    ).toBeNull();
    await user.click(screen.getByRole('menuitem', { name: 'Move to Network' }));

    await waitFor(() => {
      expect(setReportSpace).toHaveBeenCalledWith('r1', 'sp1', 'ss1', {
        notify: false,
      });
    });
  });

  it('shows a moved report in its new sub-space before the reload', async () => {
    let finish: () => void = () => {};
    const setReportSpace = jest.fn(
      () => new Promise<void>((resolve) => (finish = resolve)),
    );
    mockUseReportsMutations.mockReturnValue({
      createReport: jest.fn(),
      setReportSpace,
    });
    const user = userEvent.setup({ delay: null });
    renderAt('/app/spaces/sp1');

    await user.click(screen.getAllByLabelText('Report actions')[0]);
    await user.click(
      screen.getByRole('menuitem', { name: 'Move to Empty Group' }),
    );

    // Still in flight, yet already drawn under Empty Group in the sidebar.
    const scroll = screen.getByTestId('space-reports-scroll');
    const lists = within(scroll).getAllByRole('list');
    const emptyGroup = lists.find((list) =>
      within(list).queryByText('Empty Group'),
    );
    expect(
      within(emptyGroup!).getByRole('button', { name: 'Loose Report' }),
    ).toBeInTheDocument();
    finish();
  });

  it('moves a selection in one batch from the contents page', async () => {
    const setReportSpace = jest.fn().mockResolvedValue(undefined);
    mockUseReportsMutations.mockReturnValue({
      createReport: jest.fn(),
      setReportSpace,
    });
    const notify = jest.spyOn(reportsApiModule, 'notifyReportsUpdated');
    try {
      const user = userEvent.setup({ delay: null });
      renderAt('/app/spaces/sp1/contents');

      await user.click(
        screen.getByRole('checkbox', { name: 'Select Loose Report' }),
      );
      await user.click(
        screen.getByRole('checkbox', { name: 'Select Grouped Report' }),
      );
      const bar = screen.getByRole('region', { name: 'Selected reports' });
      expect(bar).toHaveTextContent('2 reports selected');

      await user.click(within(bar).getByRole('button', { name: 'Move to…' }));
      await user.click(screen.getByRole('menuitem', { name: 'Empty Group' }));

      await waitFor(() => expect(notify).toHaveBeenCalledTimes(1));
      expect(setReportSpace).toHaveBeenCalledWith('r1', 'sp1', 'ss2', {
        notify: false,
      });
      expect(setReportSpace).toHaveBeenCalledWith('r2', 'sp1', 'ss2', {
        notify: false,
      });
      expect(
        screen.queryByRole('region', { name: 'Selected reports' }),
      ).toBeNull();
    } finally {
      notify.mockRestore();
    }
  });

  it('selects a whole sub-space from its heading', async () => {
    const user = userEvent.setup({ delay: null });
    renderAt('/app/spaces/sp1/contents');

    await user.click(
      screen.getByRole('checkbox', { name: 'Select every report in Network' }),
    );

    expect(
      screen.getByRole('checkbox', { name: 'Select Grouped Report' }),
    ).toBeChecked();
    expect(
      screen.getByRole('region', { name: 'Selected reports' }),
    ).toHaveTextContent('1 report selected');
  });

  it('offers no selection or drag handles without reports:write', () => {
    mockUsePermissions.mockReturnValue(
      (permission: string) => permission !== 'reports:write',
    );

    renderAt('/app/spaces/sp1/contents');

    expect(screen.queryByRole('checkbox')).toBeNull();
    expect(screen.queryByRole('button', { name: /^Move / })).toBeNull();
  });

  it('marks the overview report with a star rather than a highlight', () => {
    renderAt('/app/spaces/sp1');

    // Exactly one star, on the pinned report's row and nowhere else. It sits
    // outside the row button so the report names stay aligned.
    const stars = screen.getAllByTitle('Space overview');
    expect(stars).toHaveLength(1);
    expect(stars[0].closest('li')).toHaveTextContent('Loose Report');
    expect(stars[0].closest('[role="button"]')).toBeNull();
  });
});
