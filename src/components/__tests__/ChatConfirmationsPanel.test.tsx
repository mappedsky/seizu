import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import ChatConfirmationsPanel from 'src/components/ChatConfirmationsPanel';
import type { ActionConfirmation } from 'src/hooks/useConfirmationsApi';

const theme = createTheme();

function confirmation(id: string): ActionConfirmation {
  return {
    confirmation_id: id,
    source: 'chat',
    tool_name: 'reports__delete',
    action: 'delete',
    resource_type: 'report',
    resource_id: `report-${id}`,
    arguments: { report_id: `report-${id}` },
    status: 'pending',
    created_at: '2024-01-01T00:00:00+00:00',
    expires_at: '2099-01-01T00:00:00+00:00',
  };
}

function renderPanel({
  confirmations = [],
  open = false,
  onToggle = jest.fn(),
}: {
  confirmations?: ActionConfirmation[];
  open?: boolean;
  onToggle?: () => void;
} = {}) {
  return {
    onToggle,
    ...render(
      <ThemeProvider theme={theme}>
        <ChatConfirmationsPanel
          confirmations={confirmations}
          loading={false}
          error={null}
          open={open}
          decidingId={null}
          onToggle={onToggle}
          onDecision={jest.fn()}
        />
      </ThemeProvider>,
    ),
  };
}

describe('ChatConfirmationsPanel', () => {
  afterEach(cleanup);

  it('renders as a collapsed icon by default', () => {
    renderPanel();

    expect(
      screen.getByRole('button', { name: 'Open confirmations' }),
    ).toBeInTheDocument();
    expect(screen.queryByText('No pending approvals.')).not.toBeInTheDocument();
  });

  it('shows the pending count in an error badge on the collapsed icon', () => {
    renderPanel({ confirmations: [confirmation('1'), confirmation('2')] });

    expect(
      screen.getByRole('button', {
        name: 'Open confirmations (2 pending)',
      }),
    ).toBeInTheDocument();
    expect(screen.getByText('2')).toBeInTheDocument();
  });

  it('toggles the sidebar from the collapsed icon', () => {
    const onToggle = jest.fn();
    renderPanel({ onToggle });

    fireEvent.click(screen.getByRole('button', { name: 'Open confirmations' }));

    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it('shows pending confirmations when open', () => {
    renderPanel({ confirmations: [confirmation('1')], open: true });

    expect(screen.getByText('Confirmations')).toBeInTheDocument();
    expect(screen.getByText('delete report')).toBeInTheDocument();
    // resource_id and the arguments value both contain 'report-1'
    expect(screen.getAllByText('report-1').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('Request details')).toBeInTheDocument();
    expect(screen.getByText('report_id')).toBeInTheDocument();
  });
});
