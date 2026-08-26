import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from '@testing-library/react';
import { ModelProfileSelect } from 'src/pages/ChatInterface';
import ChatInput from 'src/components/ChatInput';
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
    <ChatInput
      busy={false}
      bypassConfirmations={false}
      disabled={false}
      footerControls={
        <ModelProfileSelect
          disabled={false}
          lockedProfileId="anthropic"
          onChange={onChange}
          profileId="anthropic"
          profiles={profiles}
          reasoningEffort="medium"
        />
      }
      onBypassConfirmationsChange={() => {}}
      onStop={() => {}}
      onSubmit={() => {}}
      showBypassConfirmations
    />,
  );
  expect(screen.queryByText(/Model profile is locked/)).not.toBeInTheDocument();
  const composer = screen.getByPlaceholderText('Ask Seizu...').closest('form');
  expect(composer).not.toBeNull();
  expect(within(composer!).getByRole('combobox')).toBeInTheDocument();
  expect(within(composer!).getByRole('switch')).toBeInTheDocument();

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
