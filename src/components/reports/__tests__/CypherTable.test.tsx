import { render, screen, cleanup } from '@testing-library/react';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import CypherTable from '../CypherTable';

jest.mock('src/hooks/useCypherQuery', () => ({
  useLazyCypherQuery: jest.fn(),
}));

jest.mock('src/components/reports/CypherDetails', () => ({
  __esModule: true,
  default: function MockCypherDetails() {
    return null;
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

describe('CypherTable', () => {
  const mockRunQuery = jest.fn();

  beforeEach(() => {
    jest.clearAllMocks();
    useLazyCypherQuery.mockReturnValue([mockRunQuery, defaultState]);
  });

  afterEach(cleanup);

  it('shows error when cypher is undefined', () => {
    render(
      <Wrapper>
        <CypherTable />
      </Wrapper>,
    );
    expect(screen.getByText('Missing cypher query')).toBeInTheDocument();
  });

  it('shows table with needInputs message when needInputs is provided', () => {
    useLazyCypherQuery.mockReturnValue([mockRunQuery, { ...defaultState }]);
    render(
      <Wrapper>
        <CypherTable
          cypher="MATCH (n) RETURN n"
          needInputs={['environment', 'team']}
          columns={[{ name: 'name', label: 'Name' }]}
        />
      </Wrapper>,
    );
    expect(
      screen.getByText(/select environment, team to load results/i),
    ).toBeInTheDocument();
  });

  it('shows needInputs message instead of no columns when columns are not configured', () => {
    useLazyCypherQuery.mockReturnValue([mockRunQuery, { ...defaultState }]);
    render(
      <Wrapper>
        <CypherTable cypher="MATCH (n) RETURN n" needInputs={['environment']} />
      </Wrapper>,
    );
    expect(
      screen.getByText(/select environment to load results/i),
    ).toBeInTheDocument();
    expect(screen.queryByText(/no columns/i)).not.toBeInTheDocument();
  });

  it('shows error message when query fails', () => {
    useLazyCypherQuery.mockReturnValue([
      mockRunQuery,
      { ...defaultState, error: new Error('Query failed') },
    ]);
    render(
      <Wrapper>
        <CypherTable cypher="MATCH (n) RETURN n" />
      </Wrapper>,
    );
    expect(
      screen.getByText(/failed to load requested data/i),
    ).toBeInTheDocument();
  });

  it('shows a table skeleton while loading', () => {
    useLazyCypherQuery.mockReturnValue([
      mockRunQuery,
      { ...defaultState, loading: true },
    ]);
    render(
      <Wrapper>
        <CypherTable cypher="MATCH (n) RETURN n" caption="Loading Table" />
      </Wrapper>,
    );
    expect(screen.getByText('Loading Table')).toBeInTheDocument();
    expect(
      screen.getByTestId('cypher-table-loading-skeleton'),
    ).toBeInTheDocument();
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
    expect(screen.queryByText('No records found.')).not.toBeInTheDocument();
  });

  it('shows no records message when records array is empty', () => {
    useLazyCypherQuery.mockReturnValue([
      mockRunQuery,
      { ...defaultState, records: [], first: undefined },
    ]);
    render(
      <Wrapper>
        <CypherTable cypher="MATCH (n) RETURN n" />
      </Wrapper>,
    );
    expect(screen.getByText('No records found.')).toBeInTheDocument();
  });

  it('renders a caption when data is loaded', () => {
    const mockRecord = { name: 'test' };
    useLazyCypherQuery.mockReturnValue([
      mockRunQuery,
      { ...defaultState, records: [mockRecord], first: mockRecord },
    ]);
    render(
      <Wrapper>
        <CypherTable
          cypher="MATCH (n) RETURN n"
          caption="My Table"
          columns={[{ name: 'name', label: 'Name' }]}
        />
      </Wrapper>,
    );
    expect(screen.getByText('My Table')).toBeInTheDocument();
  });

  it('shows validation error state when queryErrors are present', () => {
    useLazyCypherQuery.mockReturnValue([
      mockRunQuery,
      { ...defaultState, queryErrors: ['Write queries are not allowed'] },
    ]);
    render(
      <Wrapper>
        <CypherTable cypher="CREATE (n) RETURN n" caption="My Table" />
      </Wrapper>,
    );
    expect(screen.getByText('Query failed.')).toBeInTheDocument();
  });

  // preloadedRecords — used by CypherGraph to share already-fetched data across tab switches.

  it('renders preloaded records without triggering a fetch', () => {
    const records = [{ name: 'Alice' }, { name: 'Bob' }];
    render(
      <Wrapper>
        <CypherTable
          cypher="MATCH (n) RETURN n"
          preloadedRecords={records}
          columns={[{ name: 'name', label: 'Name' }]}
        />
      </Wrapper>,
    );

    // The hook must have been called with undefined cypher so it never fetches.
    expect(useLazyCypherQuery).toHaveBeenCalledWith(undefined, undefined);
    // Data from preloadedRecords appears in the table.
    expect(screen.getByText('Alice')).toBeInTheDocument();
    expect(screen.getByText('Bob')).toBeInTheDocument();
  });

  it('does not show "Missing cypher query" when preloadedRecords is provided without cypher', () => {
    const records = [{ name: 'Alice' }];
    render(
      <Wrapper>
        <CypherTable
          preloadedRecords={records}
          columns={[{ name: 'name', label: 'Name' }]}
        />
      </Wrapper>,
    );

    expect(screen.queryByText('Missing cypher query')).not.toBeInTheDocument();
    expect(screen.getByText('Alice')).toBeInTheDocument();
  });

  it('shows "No records found." when preloadedRecords is an empty array', () => {
    render(
      <Wrapper>
        <CypherTable cypher="MATCH (n) RETURN n" preloadedRecords={[]} />
      </Wrapper>,
    );

    expect(screen.getByText('No records found.')).toBeInTheDocument();
  });
});
