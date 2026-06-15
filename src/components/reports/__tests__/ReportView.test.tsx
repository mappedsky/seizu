/**
 * Tests for the params-building logic in ReportView.
 *
 * The key regression: when a panel param has `input_id` but no `value` key
 * (because _strip_none removed the null value when storing in DynamoDB), the
 * old code used `!== null` which treated `undefined` as "value is set", causing
 * params to be sent as `{}` and triggering Neo4j ParameterMissing errors.
 * The fix uses `!= null` (loose) which handles both null and undefined.
 *
 * Strategy: mock useLazyCypherQuery (the data hook) rather than the panel
 * components. This avoids jest.mock() leakage into CypherCount/CypherTable/
 * FreeTextInput test files, which share bun's module registry.
 * Assertions use hook call tracking (useLazyCypherQuery.mock.calls, mockRunQuery)
 * and DOM queries instead of prop inspection.
 */
import { render, screen, cleanup } from '@testing-library/react';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import ReportView from 'src/components/ReportView';
import { Report } from 'src/config.context';

// ---------------------------------------------------------------------------
// Mock only the hook and non-tested components (no test files for these)
// ---------------------------------------------------------------------------

jest.mock('src/hooks/useCypherQuery', () => ({
  useLazyCypherQuery: jest.fn(),
}));

// CypherAutocomplete calls useLazyCypherQuery for input options — mock it so
// only the panel's hook call is counted.
jest.mock('src/components/reports/CypherAutocomplete', () => ({
  __esModule: true,
  default: () => null,
}));

// These panel types are not used in the tests below but imported by ReportView.
jest.mock('src/components/reports/CypherPie', () => ({
  __esModule: true,
  default: () => null,
}));

jest.mock('src/components/reports/CypherBar', () => ({
  __esModule: true,
  default: () => null,
}));

jest.mock('src/components/reports/CypherVerticalTable', () => ({
  __esModule: true,
  default: () => null,
}));

jest.mock('src/components/QueryString', () => ({
  getQueryStringValue: () => undefined,
}));

const { useLazyCypherQuery } = require('src/hooks/useCypherQuery');

const theme = createTheme();
function Wrapper({ children }: { children: React.ReactNode }) {
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

const QUERIES: Record<string, string> = {
  'cves-total': 'MATCH (c:CVE) RETURN count(c.id) AS total',
  'cves-severity':
    'MATCH (c:CVE) WHERE c.base_severity = $base_severity RETURN count(c) AS total',
  'cves-list':
    'MATCH (c:CVE) WHERE c.base_severity =~ ($base_severity) RETURN c',
};

function makeReport(
  panels: Report['rows'][0]['panels'],
  queries?: Record<string, string>,
): Report {
  return {
    name: 'Test',
    queries: queries ?? QUERIES,
    inputs: [
      {
        input_id: 'cve_severity',
        type: 'autocomplete',
        label: 'Base Severity',
        cypher: 'MATCH (c:CVE) RETURN DISTINCT c.base_severity AS value',
      },
    ],
    rows: [{ name: 'Row 1', panels }],
  };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('ReportView param building', () => {
  const mockRunQuery = jest.fn();

  beforeEach(() => {
    jest.clearAllMocks();
    useLazyCypherQuery.mockReturnValue([mockRunQuery, defaultState]);
  });

  afterEach(cleanup);

  it('renders the report title as a heading when enabled', () => {
    const report = makeReport([
      {
        type: 'count',
        cypher: 'cves-total',
        caption: 'Total CVEs',
      },
    ]);

    render(
      <Wrapper>
        <ReportView report={report} title="Executive Risk Report" showTitle />
      </Wrapper>,
    );

    expect(
      screen.getByRole('heading', { name: 'Executive Risk Report', level: 1 }),
    ).toBeInTheDocument();
  });

  it('does not render the report title as a heading by default', () => {
    const report = makeReport([
      {
        type: 'count',
        cypher: 'cves-total',
        caption: 'Total CVEs',
      },
    ]);

    render(
      <Wrapper>
        <ReportView report={report} title="Dashboard" />
      </Wrapper>,
    );

    expect(
      screen.queryByRole('heading', { name: 'Dashboard', level: 1 }),
    ).not.toBeInTheDocument();
  });

  it('passes static value params correctly to panel', () => {
    const report = makeReport([
      {
        type: 'count',
        cypher: 'cves-severity',
        caption: 'Critical CVEs',
        params: [{ name: 'base_severity', value: 'CRITICAL' }],
      },
    ]);

    render(
      <Wrapper>
        <ReportView report={report} title="Test" />
      </Wrapper>,
    );

    // useLazyCypherQuery is called once for the panel (autocomplete is mocked).
    // runQuery is called by the panel's useEffect with the built params dict.
    expect(mockRunQuery).toHaveBeenCalledWith(
      { base_severity: 'CRITICAL' },
      { force: false },
    );
  });

  it('adds to needInputs when value key is absent (as after DynamoDB _strip_none)', () => {
    // Regression: value key absent (undefined) should not be treated as "value is set".
    // This simulates a param stored in DynamoDB where input_id was set and value was null,
    // so _strip_none removed the value key entirely — the object only has {name, input_id}.
    const report = makeReport([
      {
        type: 'count',
        cypher: 'cves-list',
        caption: 'CVE Table',
        params: [{ name: 'base_severity', input_id: 'cve_severity' }],
      },
    ]);

    render(
      <Wrapper>
        <ReportView report={report} title="Test" />
      </Wrapper>,
    );

    // No value in varData yet → panel should not execute the query.
    expect(mockRunQuery).not.toHaveBeenCalled();
    // The panel renders the "Set <label>" message for the missing input.
    expect(screen.getByText('(Set Base Severity)')).toBeInTheDocument();
  });

  it('adds to needInputs when value is explicitly null (original YAML-loaded behavior)', () => {
    // Before DynamoDB: Pydantic model_dump() included value: null in JSON.
    // The null value should also fall through to the input_id branch.
    const report = makeReport([
      {
        type: 'count',
        cypher: 'cves-list',
        caption: 'CVE Table',
        params: [
          {
            name: 'base_severity',
            value: null as unknown as string,
            input_id: 'cve_severity',
          },
        ],
      },
    ]);

    render(
      <Wrapper>
        <ReportView report={report} title="Test" />
      </Wrapper>,
    );

    expect(mockRunQuery).not.toHaveBeenCalled();
    expect(screen.getByText('(Set Base Severity)')).toBeInTheDocument();
  });

  it('passes params correctly for progress panel with static value', () => {
    const report = makeReport([
      {
        type: 'progress',
        cypher: 'cves-severity',
        caption: 'Critical CVEs',
        threshold: 0,
        params: [{ name: 'base_severity', value: 'CRITICAL' }],
      },
    ]);

    render(
      <Wrapper>
        <ReportView report={report} title="Test" />
      </Wrapper>,
    );

    expect(mockRunQuery).toHaveBeenCalledWith(
      { base_severity: 'CRITICAL' },
      { force: false },
    );
  });

  it('passes empty params when panel has no params defined', () => {
    const report = makeReport([
      {
        type: 'count',
        cypher: 'cves-total',
        caption: 'Total CVEs',
      },
    ]);

    render(
      <Wrapper>
        <ReportView report={report} title="Test" />
      </Wrapper>,
    );

    expect(mockRunQuery).toHaveBeenCalledWith({}, { force: false });
  });

  it('passes multiple static params to panel', () => {
    const report = makeReport([
      {
        type: 'count',
        cypher: 'cves-severity',
        caption: 'CVEs',
        params: [
          { name: 'base_severity', value: 'HIGH' },
          { name: 'limit', value: '10' },
        ],
      },
    ]);

    render(
      <Wrapper>
        <ReportView report={report} title="Test" />
      </Wrapper>,
    );

    expect(mockRunQuery).toHaveBeenCalledWith(
      { base_severity: 'HIGH', limit: '10' },
      { force: false },
    );
  });

  it('resolves named query reference from report.queries', () => {
    const report = makeReport([
      {
        type: 'count',
        cypher: 'cves-total',
        caption: 'Total CVEs',
      },
    ]);

    render(
      <Wrapper>
        <ReportView report={report} title="Test" />
      </Wrapper>,
    );

    expect(useLazyCypherQuery).toHaveBeenCalledWith(
      'MATCH (c:CVE) RETURN count(c.id) AS total',
      undefined,
    );
  });

  it('passes direct Cypher string to panel when not found in report.queries', () => {
    const directCypher = 'MATCH (c:CVE) RETURN count(c.id) AS total';
    const report = makeReport(
      [
        {
          type: 'count',
          cypher: directCypher,
          caption: 'Total CVEs (direct)',
        },
      ],
      {}, // empty queries dict — no named references
    );

    render(
      <Wrapper>
        <ReportView report={report} title="Test" />
      </Wrapper>,
    );

    expect(useLazyCypherQuery).toHaveBeenCalledWith(directCypher, undefined);
  });

  it('renders markdown panel content directly without a cypher query', () => {
    const report: Report = {
      name: 'Test',
      rows: [
        {
          name: 'Markdown Row',
          panels: [
            {
              type: 'markdown',
              markdown: '## Hello\n\nSome **bold** text.',
              w: 12,
            },
          ],
        },
      ],
    };

    render(
      <Wrapper>
        <ReportView report={report} title="Test" />
      </Wrapper>,
    );

    // useLazyCypherQuery should not be called for markdown panels
    expect(useLazyCypherQuery).not.toHaveBeenCalled();
    expect(screen.getByText('Markdown Row')).toBeInTheDocument();
  });

  it('renders an actionable error when rows are missing at runtime', () => {
    const malformedReport = {
      name: 'Malformed Report',
      panels: [{ type: 'markdown', content: 'Wrong level' }],
    } as unknown as Report;

    render(
      <Wrapper>
        <ReportView report={malformedReport} title="Malformed Report" />
      </Wrapper>,
    );

    expect(screen.getByRole('alert')).toHaveTextContent(
      'rows must be an array, with panels nested under each row',
    );
  });

  it('falls back to literal string when panel.cypher is not in report.queries', () => {
    const directCypher = 'MATCH (n) RETURN count(n) AS total';
    const report = makeReport([
      {
        type: 'count',
        cypher: directCypher,
        caption: 'All Nodes',
      },
    ]);
    // QUERIES does not contain directCypher as a key, so it should be used as-is

    render(
      <Wrapper>
        <ReportView report={report} title="Test" />
      </Wrapper>,
    );

    expect(useLazyCypherQuery).toHaveBeenCalledWith(directCypher, undefined);
  });
});
