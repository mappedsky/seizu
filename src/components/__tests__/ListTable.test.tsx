import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import ListTable, {
  ListTableColumn,
  ListTableFilterGroup,
} from 'src/components/ListTable';

interface Row {
  id: string;
  name: string;
  status: 'draft' | 'public' | 'user-defined';
}

const rows: Row[] = Array.from({ length: 12 }, (_, index) => ({
  id: `row-${index + 1}`,
  name: `Row ${index + 1}`,
  status:
    index % 3 === 0 ? 'draft' : index % 3 === 1 ? 'public' : 'user-defined',
}));

const columns: ListTableColumn<Row>[] = [
  {
    key: 'name',
    label: 'Name',
    render: (row) => row.name,
  },
];

const filterGroups: ListTableFilterGroup<Row>[] = [
  {
    key: 'status',
    label: 'Status',
    options: [
      {
        key: 'draft',
        label: 'Draft',
        matches: (row) => row.status === 'draft',
      },
      {
        key: 'public',
        label: 'Public',
        matches: (row) => row.status === 'public',
      },
      {
        key: 'user-defined',
        label: 'User-defined',
        matches: (row) => row.status === 'user-defined',
      },
    ],
  },
];

describe('ListTable', () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  afterEach(() => {
    cleanup();
  });

  it('paginates rows on the client', () => {
    render(
      <ListTable
        rows={rows}
        columns={columns}
        getRowKey={(row) => row.id}
        emptyMessage="No rows."
      />,
    );

    expect(screen.getByText('Row 1')).toBeInTheDocument();
    expect(screen.queryByText('Row 11')).not.toBeInTheDocument();

    fireEvent.click(screen.getByLabelText('Go to next page'));

    expect(screen.getByText('Row 11')).toBeInTheDocument();
    expect(screen.queryByText('Row 1')).not.toBeInTheDocument();
  });

  it('restores the rows per page selection from localStorage', () => {
    window.localStorage.setItem(
      `seizu:list-table:rows-per-page:${window.location.pathname}`,
      '25',
    );

    render(
      <ListTable
        rows={rows}
        columns={columns}
        getRowKey={(row) => row.id}
        emptyMessage="No rows."
      />,
    );

    expect(screen.getByRole('combobox')).toHaveTextContent('25');
    expect(screen.getByText('Row 12')).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Go to next page' }),
    ).toBeDisabled();
  });

  it('keeps pagination controls visible when rows per page exceeds the row count', () => {
    render(
      <ListTable
        rows={rows}
        columns={columns}
        getRowKey={(row) => row.id}
        emptyMessage="No rows."
      />,
    );

    fireEvent.click(screen.getByLabelText('Go to next page'));
    fireEvent.mouseDown(screen.getByRole('combobox'));
    fireEvent.click(screen.getByRole('option', { name: '25' }));

    expect(screen.getByRole('combobox')).toBeInTheDocument();
    expect(screen.getByText('Row 1')).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Go to next page' }),
    ).toBeDisabled();
  });

  it('persists rows per page selection to localStorage', () => {
    render(
      <ListTable
        rows={rows}
        columns={columns}
        getRowKey={(row) => row.id}
        emptyMessage="No rows."
      />,
    );

    fireEvent.mouseDown(screen.getByRole('combobox'));
    fireEvent.click(screen.getByRole('option', { name: '25' }));

    expect(
      window.localStorage.getItem(
        `seizu:list-table:rows-per-page:${window.location.pathname}`,
      ),
    ).toBe('25');
  });

  it('shows an empty row when there are no rows', () => {
    render(
      <ListTable
        rows={[]}
        columns={columns}
        getRowKey={(row) => row.id}
        emptyMessage="No rows."
      />,
    );

    expect(screen.getByText('No rows.')).toBeInTheDocument();
  });

  it('filters rows by text input', () => {
    render(
      <ListTable
        rows={rows}
        columns={columns}
        getRowKey={(row) => row.id}
        emptyMessage="No rows."
      />,
    );

    fireEvent.click(screen.getByLabelText('Search'));

    fireEvent.change(screen.getByRole('textbox', { name: 'Search rows' }), {
      target: { value: 'Row 11' },
    });

    expect(screen.getByText('Row 11')).toBeInTheDocument();
    expect(screen.queryByText('Row 1')).not.toBeInTheDocument();
    expect(screen.queryByText('Row 12')).not.toBeInTheDocument();
  });

  it('filters rows by selecting a filter option', () => {
    render(
      <ListTable
        rows={rows}
        columns={columns}
        getRowKey={(row) => row.id}
        emptyMessage="No rows."
        filterGroups={filterGroups}
      />,
    );

    fireEvent.click(screen.getByLabelText('Filters'));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Draft' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Public' }));

    expect(screen.getByText('Row 1')).toBeInTheDocument();
    expect(screen.getByText('Row 2')).toBeInTheDocument();
    expect(screen.queryByText('Row 3')).not.toBeInTheDocument();
  });

  it('clears a filter group by selecting All', () => {
    render(
      <ListTable
        rows={rows}
        columns={columns}
        getRowKey={(row) => row.id}
        emptyMessage="No rows."
        filterGroups={filterGroups}
      />,
    );

    fireEvent.click(screen.getByLabelText('Filters'));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Draft' }));

    expect(screen.getByText('Row 1')).toBeInTheDocument();
    expect(screen.queryByText('Row 2')).not.toBeInTheDocument();

    fireEvent.click(screen.getByLabelText('Filters'));
    fireEvent.click(screen.getByRole('menuitem', { name: 'All' }));

    expect(screen.getByText('Row 1')).toBeInTheDocument();
    expect(screen.getByText('Row 2')).toBeInTheDocument();
  });
  // Chrome ignores min-width on cells under `table-layout: fixed`, so a table
  // that cannot fit its columns squeezes the width-less ones to a few pixels
  // instead of scrolling. The table's own minimum is what prevents that.
  it('reserves a minimum width for columns that declare no width', () => {
    render(
      <ListTable
        rows={rows}
        columns={[
          { key: 'name', label: 'Name', render: (row) => row.name },
          { key: 'status', label: 'Status', render: (row) => row.status },
          {
            key: 'sized',
            label: 'Sized',
            cellSx: { width: 200 },
            render: () => 'sized',
          },
        ]}
        getRowKey={(row) => row.id}
        emptyMessage="No rows."
      />,
    );

    // Two flexible columns at the 120px floor, plus the 200px sized column.
    expect(screen.getByRole('table')).toHaveStyle({ minWidth: '440px' });
  });

  it('solves for percentage columns when reserving the minimum width', () => {
    render(
      <ListTable
        rows={rows}
        columns={[
          { key: 'name', label: 'Name', render: (row) => row.name },
          {
            key: 'wide',
            label: 'Wide',
            cellSx: { width: '50%' },
            render: () => 'wide',
          },
          {
            key: 'sized',
            label: 'Sized',
            cellSx: { width: 180 },
            render: () => 'sized',
          },
        ]}
        getRowKey={(row) => row.id}
        emptyMessage="No rows."
      />,
    );

    // The 50% column takes half of whatever the table becomes, so the 120px
    // flexible column and the 180px sized one have to fit in the other half.
    expect(screen.getByRole('table')).toHaveStyle({ minWidth: '600px' });
  });

  it('leaves a control column out of the hover tooltip and the search text', async () => {
    const controlColumn: ListTableColumn<Row> = {
      key: 'actions',
      textual: false,
      render: (row) => (
        <button type="button" aria-label={`Actions for ${row.name}`} />
      ),
    };
    render(
      <ListTable
        rows={rows.slice(0, 1)}
        columns={[columns[0], controlColumn]}
        getRowKey={(row) => row.id}
        emptyMessage="No rows."
      />,
    );

    // The control carries its own tooltip; the cell must not add a second one
    // repeating its accessible name.
    fireEvent.mouseOver(
      screen.getByRole('button', { name: 'Actions for Row 1' }),
    );
    await waitFor(() =>
      expect(screen.queryByRole('tooltip')).not.toBeInTheDocument(),
    );

    fireEvent.click(screen.getByLabelText('Search'));
    fireEvent.change(screen.getByRole('textbox', { name: 'Search rows' }), {
      target: { value: 'Actions' },
    });
    expect(screen.queryByText('Row 1')).not.toBeInTheDocument();
  });
});

describe('ListTable column resizing', () => {
  afterEach(cleanup);

  it('exposes each resizable column edge to the keyboard', () => {
    render(
      <ListTable
        rows={rows}
        columns={[
          ...columns,
          { key: 'status', label: 'Status', render: (row) => row.status },
        ]}
        getRowKey={(row) => row.id}
        emptyMessage="None"
      />,
    );

    const handle = screen.getByRole('separator', {
      name: 'Resize Name column',
    });
    expect(handle).toHaveAttribute('tabindex', '0');
    expect(handle).toHaveAttribute('aria-orientation', 'vertical');
    // Arrow keys are taken by the handle rather than scrolling the page.
    expect(fireEvent.keyDown(handle, { key: 'ArrowRight' })).toBe(false);
    expect(fireEvent.keyDown(handle, { key: 'Enter' })).toBe(true);
  });
});

describe('ListTable on a phone', () => {
  const original = window.matchMedia;
  beforeEach(() => {
    window.localStorage.clear();
    // Below `sm` only: down() queries are max-width, up() queries min-width.
    window.matchMedia = ((query: string) => ({
      matches: query.includes('max-width'),
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    })) as typeof window.matchMedia;
  });
  afterEach(() => {
    window.matchMedia = original;
    cleanup();
  });

  interface Item {
    id: string;
    name: string;
    owner: string;
    description: string;
    version: number;
  }

  const items: Item[] = [
    { id: 'a', name: 'Alpha', owner: 'ana', description: '—', version: 3 },
  ];

  const itemColumns: ListTableColumn<Item>[] = [
    { key: 'name', label: 'Name', render: (item) => item.name },
    {
      key: 'owner',
      label: 'Owner',
      hideBelow: 'lg',
      render: (item) => item.owner,
    },
    {
      key: 'description',
      label: 'Description',
      render: (item) => item.description,
    },
    {
      key: 'version',
      label: 'Version',
      stacked: 'meta',
      render: (item) => item.version,
    },
    {
      key: 'actions',
      render: () => <button type="button">Row actions</button>,
    },
  ];

  it('lists each row with every field labelled instead of dropping columns', () => {
    render(
      <ListTable
        rows={items}
        columns={itemColumns}
        getRowKey={(item) => item.id}
        emptyMessage="None"
      />,
    );

    expect(screen.queryByRole('table')).not.toBeInTheDocument();
    const item = screen.getByRole('listitem');
    expect(item).toHaveTextContent('Alpha');
    // A column hidden below lg in the table still shows, labelled.
    expect(screen.getByRole('term')).toHaveTextContent('Owner');
    expect(screen.getByRole('definition')).toHaveTextContent('ana');
    // A placeholder dash is left out rather than shown as a value.
    expect(item).not.toHaveTextContent('Description');
    // Bookkeeping joins the closing line, a bare number with its label.
    expect(item).toHaveTextContent('Version 3');
    expect(
      screen.getByRole('button', { name: 'Row actions' }),
    ).toBeInTheDocument();
  });
});
