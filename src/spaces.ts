/**
 * Shared copy for space-related affordances.
 *
 * Lives in its own module rather than alongside a component so the pages that
 * need it do not have to import each other — importing `ReportPane` from
 * `ReportsList` would also make the reports list sensitive to any test that
 * mocks the report pane.
 */

/** Why "Move to space…" is unavailable on a space's overview report. */
export const OVERVIEW_CANNOT_MOVE =
  'The overview report cannot leave its space';

/** Why "Unpublish" is unavailable on a space's overview report. */
export const OVERVIEW_MUST_STAY_PUBLIC =
  'The space overview report must stay public';

/** Why "Delete" is unavailable on a space's overview report. */
export const OVERVIEW_DELETE_VIA_SPACE =
  'Delete the space instead — that removes its overview report too';
