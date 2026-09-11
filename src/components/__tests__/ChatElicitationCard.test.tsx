import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import ChatElicitationCard from 'src/components/ChatElicitationCard';
import type { ChatElicitation } from 'src/hooks/useChatElicitations';

const item: ChatElicitation = {
  elicitation_id: 'input-1',
  group_id: 'group-1',
  thread_id: 'thread-1',
  proxy_name: 'Example gateway',
  tool_name: 'ext__example__read',
  kind: 'form',
  message: '<img src=x onerror=alert(1)>',
  status: 'pending',
  expires_at: '2099-01-01T00:00:00Z',
  requested_schema: {
    properties: {
      answer: { type: 'string', title: 'Answer' },
      enabled: { type: 'boolean', title: 'Enabled' },
    },
    required: ['answer', 'enabled'],
  },
};

describe('ChatElicitationCard', () => {
  afterEach(cleanup);

  it('escapes remote text and sends form values only to the response handler', async () => {
    const onRespond = jest.fn().mockResolvedValue(undefined);
    const onResume = jest.fn();
    const { container } = render(
      <ChatElicitationCard
        item={item}
        busy={false}
        onRespond={onRespond}
        onResume={onResume}
      />,
    );
    expect(screen.getByText(item.message)).toBeInTheDocument();
    expect(container.querySelector('img')).toBeNull();
    fireEvent.change(screen.getByRole('textbox', { name: /Answer/ }), {
      target: { value: 'private answer' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Submit' }));
    await waitFor(() =>
      expect(onRespond).toHaveBeenCalledWith('accept', {
        answer: 'private answer',
        enabled: false,
      }),
    );
    expect(onResume).not.toHaveBeenCalled();
  });

  it.each(['Decline', 'Cancel'])(
    'does not send filled values on %s',
    async (label) => {
      const onRespond = jest.fn().mockResolvedValue(undefined);
      render(
        <ChatElicitationCard
          item={item}
          busy={false}
          onRespond={onRespond}
          onResume={jest.fn()}
        />,
      );
      fireEvent.change(screen.getByRole('textbox', { name: /Answer/ }), {
        target: { value: 'private answer' },
      });
      fireEvent.click(screen.getByRole('button', { name: label }));
      await waitFor(() =>
        expect(onRespond).toHaveBeenCalledWith(label.toLowerCase(), undefined),
      );
    },
  );

  it('requires an explicit click to visit a URL and separates completion', () => {
    const onRespond = jest.fn();
    render(
      <ChatElicitationCard
        item={{
          ...item,
          kind: 'url',
          url: 'https://gateway.example/connect?nonce=opaque',
        }}
        busy={false}
        onRespond={onRespond}
        onResume={jest.fn()}
      />,
    );
    const link = screen.getByRole('link', { name: 'Open gateway.example' });
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
    expect(link).toHaveAttribute('target', '_blank');
    expect(onRespond).not.toHaveBeenCalled();
    expect(
      screen.getByRole('button', { name: 'Completed' }),
    ).toBeInTheDocument();
  });

  it('rehydrates an answered request with a resume action and no values', async () => {
    const onResume = jest.fn().mockResolvedValue(undefined);
    render(
      <ChatElicitationCard
        item={{ ...item, status: 'accepted' }}
        busy={false}
        onRespond={jest.fn()}
        onResume={onResume}
      />,
    );
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Continue chat' }));
    await waitFor(() => expect(onResume).toHaveBeenCalledTimes(1));
  });

  it('does not offer submission after expiry', () => {
    render(
      <ChatElicitationCard
        item={{ ...item, expires_at: '2000-01-01T00:00:00Z' }}
        busy={false}
        onRespond={jest.fn()}
        onResume={jest.fn()}
      />,
    );
    expect(
      screen.queryByRole('button', { name: 'Submit' }),
    ).not.toBeInTheDocument();
    expect(screen.getByText('expired')).toBeInTheDocument();
  });
});
