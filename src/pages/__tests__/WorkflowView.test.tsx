import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import WorkflowView from 'src/pages/WorkflowView';
import * as currentUserHook from 'src/hooks/useCurrentUser';
import * as workflowsApi from 'src/hooks/useWorkflowsApi';

jest.mock('src/hooks/useWorkflowsApi', () => ({
  useWorkflow: jest.fn(),
  useWorkflowRuns: jest.fn(),
  useWorkflowRunDetail: jest.fn(),
  useWorkflowMutations: jest.fn(),
}));
jest.mock('src/hooks/usePermissions', () => ({
  usePermissions: () => () => true,
}));
jest.mock('src/hooks/useCurrentUser', () => ({
  useCurrentUser: jest.fn(),
}));
jest.mock('src/components/UserDisplay', () => ({
  __esModule: true,
  default: () => <span>Workflow owner</span>,
}));

const useWorkflow = workflowsApi.useWorkflow as jest.Mock;
const useWorkflowRuns = workflowsApi.useWorkflowRuns as jest.Mock;
const useWorkflowRunDetail = workflowsApi.useWorkflowRunDetail as jest.Mock;
const useWorkflowMutations = workflowsApi.useWorkflowMutations as jest.Mock;
const useCurrentUser = currentUserHook.useCurrentUser as jest.Mock;

beforeEach(() => {
  useCurrentUser.mockReturnValue({ user_id: 'user-1' });
  global.fetch = jest.fn().mockResolvedValue({
    ok: true,
    json: async () => ({}),
  }) as unknown as typeof fetch;
  useWorkflow.mockReturnValue({
    workflow: {
      workflow_id: 'workflow-1',
      name: 'CVE pipeline',
      stages: [
        {
          activities: [
            {
              type: 'query',
              input: null,
              output: 'findings',
              parameters: { cypher: 'RETURN 1' },
            },
          ],
        },
        {
          activities: [
            {
              type: 'log',
              input: 'findings',
              output: 'logged',
              parameters: {},
            },
          ],
        },
      ],
      schedule: { type: 'interval', interval_minutes: 60 },
      watch_scans: [],
      enabled: true,
      current_version: 3,
      created_at: '2026-07-01T00:00:00Z',
      updated_at: '2026-07-17T00:00:00Z',
      created_by: 'user-1',
      updated_by: 'user-1',
      last_run_status: 'success',
      last_run_at: '2026-07-17T00:00:00Z',
      last_errors: [],
      schedule_sync_status: 'synced',
      schedule_sync_error: null,
      schedule_synced_at: '2026-07-17T00:00:00Z',
    },
    loading: false,
    error: null,
    refresh: jest.fn(),
  });
  useWorkflowRuns.mockReturnValue({
    runs: [
      {
        workflow_id: 'seizu-workflow:workflow-1',
        run_id: 'run-1',
        workflow_name: 'seizu_configured_workflow',
        status: 'running',
        start_time: '2026-07-17T01:00:00Z',
        close_time: null,
        history_length: 8,
      },
    ],
    error: null,
    refresh: jest.fn(),
  });
  useWorkflowRunDetail.mockReturnValue(jest.fn());
  useWorkflowMutations.mockReturnValue({
    updateWorkflow: jest.fn(),
    runWorkflow: jest.fn(),
  });
});

afterEach(() => {
  cleanup();
  jest.clearAllMocks();
});

it('shows workflow status, compact stages, and recent Temporal runs', async () => {
  render(
    <MemoryRouter initialEntries={['/app/workflows/workflow-1']}>
      <Routes>
        <Route path="/app/workflows/:id" element={<WorkflowView />} />
      </Routes>
    </MemoryRouter>,
  );

  await waitFor(() => expect(global.fetch).toHaveBeenCalledTimes(1));
  expect(
    screen.getByRole('heading', { name: 'CVE pipeline' }),
  ).toBeInTheDocument();
  expect(screen.getAllByText('Running')).toHaveLength(2);
  expect(screen.getByText('2 stages · 2 activities')).toBeInTheDocument();
  expect(screen.getByText('Stage 1')).toBeInTheDocument();
  expect(screen.getByText('Stage 2')).toBeInTheDocument();
  expect(screen.getByText('No input → findings')).toBeInTheDocument();
  expect(screen.getByText('findings → logged')).toBeInTheDocument();
  expect(
    screen.getByRole('heading', { name: 'Recent Temporal runs' }),
  ).toBeInTheDocument();
  expect(screen.getByText('seizu_configured_workflow')).toBeInTheDocument();
});

it('disables mutation actions for a non-owner', async () => {
  useCurrentUser.mockReturnValue({ user_id: 'other-user' });

  render(
    <MemoryRouter initialEntries={['/app/workflows/workflow-1']}>
      <Routes>
        <Route path="/app/workflows/:id" element={<WorkflowView />} />
      </Routes>
    </MemoryRouter>,
  );

  await waitFor(() => expect(global.fetch).toHaveBeenCalledTimes(1));
  expect(screen.getByRole('button', { name: 'Edit' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Run now' })).toBeDisabled();
});
