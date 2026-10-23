import { render, screen, cleanup, within } from '@testing-library/react';
import GraphDetailPanel from '../GraphDetailPanel';

afterEach(cleanup);

describe('GraphDetailPanel', () => {
  it('renders node metadata separately from node properties', () => {
    render(
      <GraphDetailPanel
        type="node"
        data={{
          id: 'domain-alert-id',
          neo4j_id: 42,
          labels: ['GitHubDependabotAlert'],
          properties: {
            id: 'domain-alert-id',
            package_name: 'openssl',
          },
          label: 'Alert A',
          group: 'GitHubDependabotAlert',
        }}
      />,
    );

    expect(screen.getByText('Metadata')).toBeInTheDocument();
    expect(screen.getByText('Properties')).toBeInTheDocument();
    expect(screen.getAllByText('Key')).toHaveLength(2);
    expect(screen.getAllByText('Value')).toHaveLength(2);
    expect(screen.getByText('Neo4j ID')).toBeInTheDocument();
    expect(screen.getByText('42')).toBeInTheDocument();
    expect(screen.getByText('domain-alert-id')).toBeInTheDocument();
    expect(screen.getByText('openssl')).toBeInTheDocument();
    expect(screen.getByRole('list')).toBeInTheDocument();
    expect(
      within(screen.getByRole('list')).getByText('GitHubDependabotAlert'),
    ).toBeInTheDocument();
    expect(screen.queryByText('Additional Fields')).not.toBeInTheDocument();
  });

  it('renders relationship metadata separately from relationship properties', () => {
    render(
      <GraphDetailPanel
        type="link"
        data={{
          neo4j_id: 7,
          source: 42,
          target: 43,
          type: 'AFFECTS',
          properties: {
            id: 'domain-rel-id',
          },
        }}
      />,
    );

    expect(screen.getByText('Metadata')).toBeInTheDocument();
    expect(screen.getByText('Properties')).toBeInTheDocument();
    expect(screen.getByText('AFFECTS')).toBeInTheDocument();
    expect(screen.getByText('domain-rel-id')).toBeInTheDocument();
  });
});
