import { useCallback, useEffect, useRef } from 'react';

export interface ChatHumanInputResume {
  kind: 'confirmation' | 'elicitation';
  id: string;
  threadId: string;
}

interface HiddenResumeMessage {
  id: string;
  role: 'user';
  metadata: { seizu_hidden: true };
  parts: [];
}

type SendResume = (
  message: HiddenResumeMessage,
  options: { body: Record<string, unknown> },
) => void | Promise<void>;

/** Dispatch an ID-only continuation after the caller has resolved its input. */
export function useChatHumanInputResume(
  threadId: string | null,
  sendMessage: SendResume,
  touchSession: (threadId: string) => void,
) {
  const currentThreadRef = useRef(threadId);
  currentThreadRef.current = threadId;
  const mountedRef = useRef(false);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  return useCallback(
    async (request: ChatHumanInputResume) => {
      // An answer may finish saving after navigation to another conversation.
      if (
        !mountedRef.current ||
        !currentThreadRef.current ||
        request.threadId !== currentThreadRef.current
      ) {
        throw new Error('Return to the original conversation to resume it.');
      }
      const body =
        request.kind === 'confirmation'
          ? { resume_confirmation_id: request.id }
          : { resume_elicitation_id: request.id };
      touchSession(request.threadId);
      await sendMessage(
        {
          id: `resume-${request.id}`,
          role: 'user',
          metadata: { seizu_hidden: true },
          parts: [],
        },
        { body },
      );
    },
    [sendMessage, touchSession],
  );
}
