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
      onStop={() => {}}
      onSubmit={() => {}}
    />,
  );
  expect(screen.queryByText(/Model profile is locked/)).not.toBeInTheDocument();
  const composer = screen.getByPlaceholderText('Ask Seizu...').closest('form');
  expect(composer).not.toBeNull();
  expect(within(composer!).getByRole('combobox')).toBeInTheDocument();
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

it('sends on Enter and keeps Shift+Enter for a new line', () => {
  const onSubmit = jest.fn();
  render(
    <ChatInput
      busy={false}
      disabled={false}
      onStop={() => {}}
      onSubmit={onSubmit}
    />,
  );
  const field = screen.getByPlaceholderText('Ask Seizu...');
  fireEvent.change(field, { target: { value: 'Map my graph' } });

  fireEvent.keyDown(field, { key: 'Enter', shiftKey: true });
  expect(onSubmit).not.toHaveBeenCalled();

  fireEvent.keyDown(field, { key: 'Enter' });
  expect(onSubmit).toHaveBeenCalledWith('Map my graph');
  expect(field).toHaveValue('');
});

it.each([
  ['Ctrl+Enter', { ctrlKey: true }],
  ['Cmd+Enter', { metaKey: true }],
])('inserts a new line at the caret on %s', (_name, modifier) => {
  const onSubmit = jest.fn();
  render(
    <ChatInput
      busy={false}
      disabled={false}
      onStop={() => {}}
      onSubmit={onSubmit}
    />,
  );
  const field = screen.getByPlaceholderText(
    'Ask Seizu...',
  ) as HTMLTextAreaElement;
  fireEvent.change(field, { target: { value: 'Map my graph' } });
  field.setSelectionRange(3, 3);

  fireEvent.keyDown(field, { key: 'Enter', ...modifier });

  expect(onSubmit).not.toHaveBeenCalled();
  expect(field).toHaveValue('Map\n my graph');
});
