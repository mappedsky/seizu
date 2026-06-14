import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import CypherProgress from '../CypherProgress';

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

describe('CypherProgress', () => {
  const mockRunQuery = jest.fn();

  beforeEach(() => {
    jest.clearAllMocks();
    useLazyCypherQuery.mockReturnValue([mockRunQuery, defaultState]);
  });

  afterEach(cleanup);

  it('shows error when cypher is undefined', () => {
    render(
      <Wrapper>
        <CypherProgress caption="Test Progress" />
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
        <CypherProgress
          cypher="MATCH (n) RETURN count(n) as numerator, count(n) as denominator"
          caption="Test Progress"
          needInputs={['team']}
        />
      </Wrapper>,
    );
    expect(screen.getByText('N/A')).toBeInTheDocument();
    expect(screen.getByText('(Set team)')).toBeInTheDocument();
  });

  it('shows error message when query fails', () => {
    useLazyCypherQuery.mockReturnValue([
      mockRunQuery,
      { ...defaultState, error: new Error('Query failed') },
    ]);
    render(
      <Wrapper>
        <CypherProgress
          cypher="MATCH (n) RETURN count(n) as numerator, count(n) as denominator"
          caption="Test Progress"
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
        <CypherProgress
          cypher="MATCH (n) RETURN count(n) as numerator, count(n) as denominator"
          caption="Test Progress"
        />
      </Wrapper>,
    );
    expect(screen.getByText('N/A')).toBeInTheDocument();
  });

  it('renders circular progress when data is loaded', () => {
    useLazyCypherQuery.mockReturnValue([
      mockRunQuery,
      {
        ...defaultState,
        records: [{ numerator: 75, denominator: 100 }],
        first: { numerator: 75, denominator: 100 },
      },
    ]);
    render(
      <Wrapper>
        <CypherProgress
          cypher="MATCH (n) RETURN count(n) as numerator, count(n) as denominator"
          caption="Progress Caption"
        />
      </Wrapper>,
    );
    expect(screen.getByText('Progress Caption')).toBeInTheDocument();
    expect(screen.getByText('75%')).toBeInTheDocument();
  });

  it('renders the info button in the top-right when data is loaded', () => {
    useLazyCypherQuery.mockReturnValue([
      mockRunQuery,
      {
        ...defaultState,
        records: [{ numerator: 75, denominator: 100 }],
        first: { numerator: 75, denominator: 100 },
      },
    ]);
    const { container } = render(
      <Wrapper>
        <CypherProgress
          cypher="MATCH (n) RETURN count(n) as numerator, count(n) as denominator"
          caption="Test Progress"
        />
      </Wrapper>,
    );
    expect(container.querySelector('.panel-info-btn')).toBeInTheDocument();
  });

  it('opens the details dialog when the info button is clicked', () => {
    useLazyCypherQuery.mockReturnValue([
      mockRunQuery,
      {
        ...defaultState,
        records: [{ numerator: 75, denominator: 100 }],
        first: { numerator: 75, denominator: 100 },
      },
    ]);
    const { container } = render(
      <Wrapper>
        <CypherProgress
          cypher="MATCH (n) RETURN count(n) as numerator, count(n) as denominator"
          caption="Test Progress"
          details={{ type: 'progress', caption: 'Test Progress' }}
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
        <CypherProgress cypher="CREATE (n) RETURN n" caption="Test Progress" />
      </Wrapper>,
    );
    expect(screen.getByText('Query failed')).toBeInTheDocument();
  });
});
