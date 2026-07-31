import { useParams } from 'react-router-dom';
import ReportPane from 'src/components/ReportPane';

/**
 * The standalone report route. The rendering, editing, and actions all live in
 * ReportPane so the space detail page can host the same experience inline.
 */
function Reports() {
  const { id } = useParams();
  // Keyed by id: ReportPane holds displayed-report and edit state, and
  // EditableReportView seeds its editor once on mount. Reusing the instance
  // across a report switch could save one report's edits against another's id.
  return <ReportPane key={id} id={id} />;
}

export default Reports;
