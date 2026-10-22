import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import * as graphSchema from 'src/hooks/useGraphSchema';
import * as historyApi from 'src/hooks/useQueryHistory';
import QueryConsoleSchemaPanel, {
  filterSchema,
} from 'src/components/QueryConsoleSchemaPanel';

const theme = createTheme();

const schema = {
  labels: ['CVE', 'GitHubRepository', 'GitHubBranch'],
  relationship_types: ['AFFECTS', 'BRANCH'],
  property_keys: ['id', 'name'],
  indexes: [
    {
      name: 'cve_id',
      type: 'RANGE',
      entity_type: 'NODE',
      labels_or_types: ['CVE'],
      properties: ['id'],
      state: 'ONLINE',
    },
  ],
};

describe('filterSchema', () => {
  const all = {
    labels: schema.labels,
    rels: schema.relationship_types,
    props: schema.property_keys,
    indexes: schema.indexes,
  };

  it('keeps everything for an empty filter', () => {
    expect(filterSchema(all, '  ')).toBe(all);
  });

  it('matches names case-insensitively across every section', () => {
    const result = filterSchema(all, 'branch');
    expect(result.labels).toEqual(['GitHubBranch']);
    expect(result.rels).toEqual(['BRANCH']);
    expect(result.props).toEqual([]);
    expect(result.indexes).toEqual([]);
  });

  it('matches an index by its target as well as its name', () => {
    expect(filterSchema(all, 'cve').indexes).toHaveLength(1);
    expect(filterSchema(all, 'range').indexes).toHaveLength(1);
  });
});

describe('QueryConsoleSchemaPanel filter', () => {
  beforeEach(() => {
    jest.spyOn(graphSchema, 'useGraphSchema').mockReturnValue({
      loading: false,
      error: null,
      schema,
      fetchSchema: jest.fn(),
    });
    jest.spyOn(historyApi, 'useQueryHistory').mockReturnValue({
      loading: false,
      error: null,
      data: null,
      fetchHistory: jest.fn(),
    } as unknown as ReturnType<typeof historyApi.useQueryHistory>);
  });

  afterEach(() => {
    cleanup();
    jest.restoreAllMocks();
  });

  function renderPanel() {
    return render(
      <ThemeProvider theme={theme}>
        <QueryConsoleSchemaPanel
          open
          onToggle={jest.fn()}
          onQuerySelect={jest.fn()}
          onHistorySelect={jest.fn()}
        />
      </ThemeProvider>,
    );
  }

  it('narrows the sections and drops those with no match', () => {
    renderPanel();
    fireEvent.change(screen.getByLabelText('Filter schema'), {
      target: { value: 'github' },
    });
    expect(screen.getByText('NODES (2 of 3)')).toBeTruthy();
    expect(screen.queryByText('CVE')).toBeNull();
    expect(screen.queryByText(/^RELATIONSHIPS/)).toBeNull();
  });

  it('says so when nothing matches, and Escape clears the filter', () => {
    renderPanel();
    const field = screen.getByLabelText('Filter schema');
    fireEvent.change(field, { target: { value: 'zzz' } });
    expect(screen.getByText('Nothing in the schema matches.')).toBeTruthy();
    fireEvent.keyDown(field, { key: 'Escape' });
    expect(screen.getByText('NODES (3)')).toBeTruthy();
  });
});
