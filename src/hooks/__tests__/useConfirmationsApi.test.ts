import {
  type ActionConfirmation,
  effectiveConfirmationStatus,
  isConfirmationExpired,
} from 'src/hooks/useConfirmationsApi';

function makeConfirmation(
  overrides: Partial<ActionConfirmation>,
): ActionConfirmation {
  return {
    confirmation_id: 'a'.repeat(32),
    source: 'mcp',
    tool_name: 'toolsets__update_tool',
    action: 'update',
    resource_type: 'tool',
    resource_id: 'find_escalation_points',
    arguments: {},
    status: 'pending',
    created_at: new Date(Date.now() - 60_000).toISOString(),
    expires_at: new Date(Date.now() + 60_000).toISOString(),
    ...overrides,
  };
}

describe('isConfirmationExpired', () => {
  it('is false when expires_at is in the future', () => {
    expect(
      isConfirmationExpired({
        expires_at: new Date(Date.now() + 5_000).toISOString(),
      }),
    ).toBe(false);
  });

  it('is true when expires_at is in the past', () => {
    expect(
      isConfirmationExpired({
        expires_at: new Date(Date.now() - 5_000).toISOString(),
      }),
    ).toBe(true);
  });
});

describe('effectiveConfirmationStatus', () => {
  it('reports a past-due pending confirmation as expired', () => {
    const confirmation = makeConfirmation({
      status: 'pending',
      expires_at: new Date(Date.now() - 1_000).toISOString(),
    });
    expect(effectiveConfirmationStatus(confirmation)).toBe('expired');
  });

  it('leaves a not-yet-expired pending confirmation pending', () => {
    const confirmation = makeConfirmation({
      status: 'pending',
      expires_at: new Date(Date.now() + 60_000).toISOString(),
    });
    expect(effectiveConfirmationStatus(confirmation)).toBe('pending');
  });

  it('never overrides an already-decided status, even past expiry', () => {
    const confirmation = makeConfirmation({
      status: 'approved',
      expires_at: new Date(Date.now() - 60_000).toISOString(),
    });
    expect(effectiveConfirmationStatus(confirmation)).toBe('approved');
  });
});
