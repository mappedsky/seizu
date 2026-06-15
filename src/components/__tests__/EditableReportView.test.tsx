import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import EditableReportView from 'src/components/EditableReportView';
import { Report } from 'src/config.context';

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

describe('EditableReportView', () => {
  afterEach(cleanup);

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
});
