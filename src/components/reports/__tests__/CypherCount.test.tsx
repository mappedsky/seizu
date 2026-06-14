import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import CypherCount from '../CypherCount';

jest.mock('src/hooks/useCypherQuery', () => ({
  useLazyCypherQuery: jest.fn(),
}));

jest.mock('src/components/reports/CypherDetails', () => ({
  __esModule: true,
  default: function MockCypherDetails({ open }: { open: boolean }) {
    return open ? <div data-testid="details-dialog">Details</div> : null;
  },
}));

const { useLazyCypherQuery } = require('src/hooks/useCypherQuery');

const theme = createTheme();

function Wrapper({ children }) {
  return <ThemeProvider theme={theme}>{children}</ThemeProvider>;
}

const defaultState = {
  loading: false,
  error: null,
  records: undefined,
  first: undefined,
  warnings: [],
  queryErrors: [],
};

describe('CypherCount', () => {
  const mockRunQuery = jest.fn();

  beforeEach(() => {
    jest.clearAllMocks();
    useLazyCypherQuery.mockReturnValue([mockRunQuery, defaultState]);
  });

  afterEach(cleanup);

  it('shows error when cypher is undefined', () => {
    render(
      <Wrapper>
        <CypherCount caption="Test Count" />
      </Wrapper>,
    );
    expect(screen.getByText('Missing cypher query')).toBeInTheDocument();
  });

  it('shows N/A with needInputs message when needInputs is provided', () => {
    useLazyCypherQuery.mockReturnValue([
      mockRunQuery,
      { ...defaultState, records: [], first: undefined },
    ]);
    render(
      <Wrapper>
        <CypherCount
          cypher="MATCH (n) RETURN count(n) as total"
          caption="Test Count"
          needInputs={['param1', 'param2']}
        />
      </Wrapper>,
    );
    expect(screen.getByText('N/A')).toBeInTheDocument();
    expect(screen.getByText('(Set param1, param2)')).toBeInTheDocument();
  });

  it('shows a count skeleton when loading', () => {
    useLazyCypherQuery.mockReturnValue([
      mockRunQuery,
      { ...defaultState, loading: true },
    ]);
    render(
      <Wrapper>
        <CypherCount
          cypher="MATCH (n) RETURN count(n) as total"
          caption="Test Count"
        />
      </Wrapper>,
    );
    expect(
      screen.getByTestId('count-panel-loading-skeleton'),
    ).toBeInTheDocument();
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
  });

  it('shows error message when query fails', () => {
    useLazyCypherQuery.mockReturnValue([
      mockRunQuery,
      { ...defaultState, error: new Error('Query failed') },
    ]);
    render(
      <Wrapper>
        <CypherCount
          cypher="MATCH (n) RETURN count(n) as total"
          caption="Test Count"
        />
      </Wrapper>,
    );
    expect(
      screen.getByText(/failed to load requested data/i),
    ).toBeInTheDocument();
  });

  it('shows N/A when records exist but first is undefined', () => {
    useLazyCypherQuery.mockReturnValue([
      mockRunQuery,
      { ...defaultState, records: [], first: undefined },
    ]);
    render(
      <Wrapper>
        <CypherCount
          cypher="MATCH (n) RETURN count(n) as total"
          caption="Test Count"
        />
      </Wrapper>,
    );
    expect(screen.getByText('N/A')).toBeInTheDocument();
  });

  it('renders the count value when data is loaded', () => {
    useLazyCypherQuery.mockReturnValue([
      mockRunQuery,
      { ...defaultState, records: [{ total: 42 }], first: { total: 42 } },
    ]);
    render(
      <Wrapper>
        <CypherCount
          cypher="MATCH (n) RETURN count(n) as total"
          caption="Test Count"
        />
      </Wrapper>,
    );
    expect(screen.getByText('Test Count')).toBeInTheDocument();
    expect(screen.getByText('42')).toBeInTheDocument();
  });

  it('renders the info button in the top-right when data is loaded', () => {
    useLazyCypherQuery.mockReturnValue([
      mockRunQuery,
      { ...defaultState, records: [{ total: 5 }], first: { total: 5 } },
    ]);
    const { container } = render(
      <Wrapper>
        <CypherCount
          cypher="MATCH (n) RETURN count(n) as total"
          caption="Test Count"
        />
      </Wrapper>,
    );
    expect(container.querySelector('.panel-info-btn')).toBeInTheDocument();
  });

  it('opens the details dialog when the info button is clicked', () => {
    useLazyCypherQuery.mockReturnValue([
      mockRunQuery,
      { ...defaultState, records: [{ total: 5 }], first: { total: 5 } },
    ]);
    const { container } = render(
      <Wrapper>
        <CypherCount
          cypher="MATCH (n) RETURN count(n) as total"
          caption="Test Count"
          details={{ type: 'count', caption: 'Test Count' }}
        />
      </Wrapper>,
    );
    fireEvent.click(container.querySelector('.panel-info-btn')!);
    expect(screen.getByTestId('details-dialog')).toBeInTheDocument();
  });

  it('shows validation error state when queryErrors are present', () => {
    useLazyCypherQuery.mockReturnValue([
      mockRunQuery,
      { ...defaultState, queryErrors: ['Write queries are not allowed'] },
    ]);
    render(
      <Wrapper>
        <CypherCount cypher="CREATE (n) RETURN n" caption="Test Count" />
      </Wrapper>,
    );
    expect(screen.getByText('Query failed')).toBeInTheDocument();
  });
});
