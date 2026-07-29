import { useParams } from 'react-router-dom';
import ReportPane from 'src/components/ReportPane';

/**
 * The standalone report route. The rendering, editing, and actions all live in
 * ReportPane so the space detail page can host the same experience inline.
 */
function Reports() {
  const { id } = useParams();
  return <ReportPane id={id} />;
}

export default Reports;
