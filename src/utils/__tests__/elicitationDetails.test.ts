import { reconcileElicitationDetail } from '../elicitationDetails';

describe('elicitation detail continuation', () => {
  const original = {
    status: 'blocked',
    body: JSON.stringify({ elicitation_ids: ['first', 'second'] }),
  };
  it('shows a parked call as awaiting input', () => {
    expect(reconcileElicitationDetail(original, []).status).toBe('awaiting');
  });
  it('matches any question in the original group and preserves failure outcomes', () => {
    const outcome = {
      elicitation_ids: ['second'],
      elicitation_resumed: true,
      status: 'completed',
      body: 'Finished',
    };
    expect(reconcileElicitationDetail(original, [outcome])).toMatchObject({
      status: 'completed',
      body: 'Finished',
    });
    expect(
      reconcileElicitationDetail(original, [
        outcome,
        {
          ...outcome,
          status: 'blocked',
          body: 'Failed',
        },
      ]),
    ).toMatchObject({ status: 'blocked', body: 'Failed' });
    expect(
      reconcileElicitationDetail(original, [
        {
          ...outcome,
          elicitation_ids: ['unrelated'],
        },
      ]).status,
    ).toBe('awaiting');
  });
});
