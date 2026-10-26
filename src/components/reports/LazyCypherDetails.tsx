import { lazy, Suspense, useState } from 'react';
import type { CypherDetailsProps } from 'src/components/reports/CypherDetails';

// The details dialog carries the data-table code; a panel fetches it the
// first time its dialog opens rather than with the report.
const CypherDetails = lazy(
  () => import('src/components/reports/CypherDetails'),
);

/**
 * `CypherDetails`, fetched on first open. It stays mounted once opened so its
 * close transition plays like any other dialog.
 */
export default function LazyCypherDetails(props: CypherDetailsProps) {
  const [opened, setOpened] = useState(props.open);
  if (props.open && !opened) setOpened(true);
  if (!opened) return null;
  return (
    <Suspense fallback={null}>
      <CypherDetails {...props} />
    </Suspense>
  );
}
