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

it('shows only the locked profile with lowercase reasoning levels', () => {
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
  expect(screen.getByText('Anthropic · medium')).toBeInTheDocument();
  expect(screen.queryByText('Model and reasoning')).not.toBeInTheDocument();

  fireEvent.mouseDown(
    screen.getByRole('combobox', { name: 'Model and reasoning' }),
  );
  const listbox = screen.getByRole('listbox');
  expect(within(listbox).getByText('Anthropic (default)')).toBeInTheDocument();
  expect(within(listbox).queryByText('DeepSeek')).not.toBeInTheDocument();
  expect(
    within(listbox).getByRole('option', { name: 'low' }),
  ).toBeInTheDocument();
  expect(
    within(listbox).getByRole('option', { name: 'medium' }),
  ).toBeInTheDocument();
  const highOption = within(listbox).getByRole('option', { name: 'high' });

  fireEvent.click(highOption);
  expect(onChange).toHaveBeenCalledWith('anthropic', 'high');
});

it('grows the chat composer with its text up to twice the default height', () => {
  render(
    <ChatInput
      busy={false}
      disabled={false}
      onStop={() => {}}
      onSubmit={() => {}}
    />,
  );
  const textarea = screen.getByPlaceholderText('Ask Seizu...');
  Object.defineProperty(textarea, 'clientHeight', {
    configurable: true,
    value: 80,
  });
  Object.defineProperty(textarea, 'scrollHeight', {
    configurable: true,
    value: 400,
  });

  fireEvent.change(textarea, { target: { value: 'A long question' } });

  expect(screen.getByTestId('chat-composer')).toHaveStyle({ height: '280px' });
});
