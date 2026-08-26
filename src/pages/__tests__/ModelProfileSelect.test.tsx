import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from '@testing-library/react';
import { ModelProfileSelect } from 'src/pages/ChatInterface';
import type { SelectableModelProfile } from 'src/hooks/useModelProfilesApi';

const profiles: SelectableModelProfile[] = [
  {
    profile_id: 'anthropic',
    name: 'Anthropic',
    description: '',
    is_default: true,
    default_reasoning_effort: 'medium',
    reasoning_efforts: ['low', 'medium', 'high'],
    run_cost_budget_usd: 2,
    effective_cost_budget_usd: 2,
  },
  {
    profile_id: 'deepseek',
    name: 'DeepSeek',
    description: '',
    is_default: false,
    default_reasoning_effort: 'low',
    reasoning_efforts: ['low', 'medium', 'high'],
    run_cost_budget_usd: 1,
    effective_cost_budget_usd: 1,
  },
];

afterEach(cleanup);

it('groups reasoning levels by profile and locks other profile families', () => {
  const onChange = jest.fn();
  render(
    <ModelProfileSelect
      disabled={false}
      lockedProfileId="anthropic"
      onChange={onChange}
      profileId="anthropic"
      profiles={profiles}
      reasoningEffort="medium"
    />,
  );
  expect(
    screen.getByText(
      'Model profile is locked for this conversation; reasoning may still change.',
    ),
  ).toBeInTheDocument();

  fireEvent.mouseDown(
    screen.getByRole('combobox', { name: 'Model and reasoning' }),
  );
  const listbox = screen.getByRole('listbox');
  expect(within(listbox).getByText('Anthropic (default)')).toBeInTheDocument();
  expect(within(listbox).getByText('DeepSeek')).toBeInTheDocument();
  const highOptions = within(listbox).getAllByRole('option', { name: 'High' });
  expect(highOptions[0]).not.toHaveAttribute('aria-disabled', 'true');
  expect(highOptions[1]).toHaveAttribute('aria-disabled', 'true');

  fireEvent.click(highOptions[0]);
  expect(onChange).toHaveBeenCalledWith('anthropic', 'high');
});
