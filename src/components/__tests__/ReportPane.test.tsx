import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import ReportPane from 'src/components/ReportPane';
import * as reportsApiModule from 'src/hooks/useReportsApi';
import * as permissionsModule from 'src/hooks/usePermissions';

jest.mock('src/hooks/usePermissions', () => ({
  usePermissionState: jest.fn(),
}));

const theme = createTheme();
const mockUsePermissionState =
  permissionsModule.usePermissionState as jest.MockedFunction<
    typeof permissionsModule.usePermissionState
  >;

function Wrapper({ children }: { children: React.ReactNode }) {
  return (
    <MemoryRouter initialEntries={['/app/reports/r1?edit=true']}>
      <ThemeProvider theme={theme}>
        <Routes>
          <Route path="/app/reports/:id" element={children} />
        </Routes>
      </ThemeProvider>
    </MemoryRouter>
  );
}

describe('ReportPane', () => {
  let mockUseReport: jest.Mock;
  let mockUseReportsMutations: jest.Mock;
  let saveReportVersion: jest.Mock;

  beforeEach(() => {
    jest.clearAllMocks();
    saveReportVersion = jest.fn().mockResolvedValue({
      report_id: 'r1',
      name: 'Renamed Report',
      version: 2,
      config: { schema_version: 1, name: 'Renamed Report', rows: [] },
      created_at: '2026-01-02T00:00:00Z',
      created_by: 'owner',
      report_created_by: 'owner',
      report_updated_by: 'owner',
      access: { scope: 'private' },
      comment: null,
      query_capabilities: {},
    });
    mockUseReport = jest.spyOn(
      reportsApiModule,
      'useReport',
    ) as unknown as jest.Mock;
    mockUseReportsMutations = jest.spyOn(
      reportsApiModule,
      'useReportsMutations',
    ) as unknown as jest.Mock;
    mockUsePermissionState.mockReturnValue({
      hasPermission: () => true,
      loading: false,
      currentUser: {
        user_id: 'owner',
        sub: 'owner',
        iss: 'test',
        email: 'owner@example.com',
        display_name: 'Owner',
        created_at: '2026-01-01T00:00:00Z',
        last_login: '2026-01-02T00:00:00Z',
        archived_at: null,
        permissions: [],
      },
    });
    mockUseReport.mockReturnValue({
      report: {
        schema_version: 1,
        rows: [],
      },
      name: 'Metadata Report Name',
      reportVersion: {
        report_id: 'r1',
        name: 'Metadata Report Name',
        version: 1,
        config: { schema_version: 1, rows: [] },
        created_at: '2026-01-01T00:00:00Z',
        created_by: 'owner',
        report_created_by: 'owner',
        report_updated_by: 'owner',
        access: { scope: 'private' },
        comment: null,
      },
      queryCapabilities: {},
      loading: false,
      error: null,
    });
    mockUseReportsMutations.mockReturnValue({
      saveReportVersion,
      cloneReport: jest.fn(),
      updateReportVisibility: jest.fn(),
    });
  });

  afterEach(() => {
    cleanup();
    mockUseReport.mockRestore?.();
    mockUseReportsMutations.mockRestore?.();
  });

  it('populates edit-mode report name from report metadata when config name is absent', () => {
    render(<ReportPane id="r1" />, { wrapper: Wrapper });

    expect(screen.getByLabelText('Report name')).toHaveValue(
      'Metadata Report Name',
    );
  });

  it('saves the edited report name through the new report version', async () => {
    render(<ReportPane id="r1" />, { wrapper: Wrapper });

    fireEvent.change(screen.getByLabelText('Report name'), {
      target: { value: 'Renamed Report' },
    });
    fireEvent.click(screen.getByRole('button', { name: /save version/i }));

    await waitFor(() => expect(saveReportVersion).toHaveBeenCalled());
    expect(saveReportVersion).toHaveBeenCalledWith(
      'r1',
      expect.objectContaining({ name: 'Renamed Report' }),
      undefined,
      true,
    );
  });
});
