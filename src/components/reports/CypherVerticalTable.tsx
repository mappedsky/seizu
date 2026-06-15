import { useEffect, useRef, useState } from 'react';
import {
  Box,
  Divider,
  IconButton,
  List,
  ListItem,
  Paper,
  Typography,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableRow,
} from '@mui/material';
import Info from '@mui/icons-material/Info';
import Error from '@mui/icons-material/Error';

import { useLazyCypherQuery, QueryRecord } from 'src/hooks/useCypherQuery';

/**
 * Flatten a query record into a plain key→value map.
 * Handles: bare multi-column records, single-key plain objects, and Neo4j node objects (with .properties).
 */
function flattenRecord(record: QueryRecord): QueryRecord {
  const keys = Object.keys(record);
  if (keys.length === 1) {
    const val = record[keys[0]];
    if (val && typeof val === 'object' && !Array.isArray(val)) {
      const obj = val as QueryRecord;
      if (
        obj.properties &&
        typeof obj.properties === 'object' &&
        !Array.isArray(obj.properties)
      ) {
        return obj.properties as QueryRecord;
      }
      return obj;
    }
  }
  return record;
}
import CypherDetails from 'src/components/reports/CypherDetails';
import { VerticalTableSkeleton } from 'src/components/reports/PanelLoadingSkeletons';
import QueryValidationBadge from 'src/components/reports/QueryValidationBadge';

const fillSx = {
  height: '100%',
  display: 'flex',
  flexDirection: 'column' as const,
  minHeight: 0,
};

const scrollBodySx = {
  flex: 1,
  minHeight: 0,
  overflow: 'auto',
};

const autoBodySx = {
  flex: 'none',
};

interface CypherVerticalTableProps {
  cypher?: string;
  params?: Record<string, unknown>;
  id?: string;
  details?: Record<string, unknown>;
  needInputs?: string[];
  autoHeight?: boolean;
  reportQueryToken?: string;
  refreshKey?: number;
  onTokenExpired?: () => void;
}

export default function CypherVerticalTable({
  cypher,
  params,
  id,
  details,
  needInputs,
  autoHeight = false,
  reportQueryToken,
  refreshKey,
  onTokenExpired,
}: CypherVerticalTableProps) {
  const [open, setOpen] = useState(false);
  const handleClickOpen = () => {
    setOpen(true);
  };

  const [
    runQuery,
    { loading, error, records, warnings, queryErrors, tokenExpired },
  ] = useLazyCypherQuery(cypher, reportQueryToken);

  const runQueryRef = useRef(runQuery);
  runQueryRef.current = runQuery;
  const needInputsRef = useRef(needInputs);
  needInputsRef.current = needInputs;

  useEffect(() => {
    if (
      needInputsRef.current === undefined ||
      needInputsRef.current.length === 0
    ) {
      runQueryRef.current(params, { force: (refreshKey ?? 0) > 0 });
    }
  }, [cypher, params, refreshKey]);

  useEffect(() => {
    if (tokenExpired) {
      onTokenExpired?.();
    }
  }, [tokenExpired, onTokenExpired]);

  if (cypher === undefined) {
    return (
      <Box sx={fillSx}>
        <Error />
        <Typography variant="body2">Missing cypher query</Typography>
      </Box>
    );
  }

  if (needInputs !== undefined && needInputs.length > 0) {
    return (
      <Box sx={fillSx}>
        <Typography variant="body2">
          Please set {needInputs.join(', ')}
        </Typography>
      </Box>
    );
  }

  if (error) {
    console.log(error);
    return (
      <Box sx={fillSx}>
        <Typography variant="body2">
          Failed to load requested data, please reload.
        </Typography>
      </Box>
    );
  }

  if (queryErrors.length > 0) {
    return (
      <Box sx={fillSx}>
        <Typography gutterBottom variant="h4" component="div">
          <QueryValidationBadge errors={queryErrors} warnings={warnings} />
        </Typography>
        <Typography variant="body2">Query failed.</Typography>
      </Box>
    );
  }

  if (loading || records === undefined) {
    return (
      <Box sx={fillSx}>
        <VerticalTableSkeleton />
      </Box>
    );
  }

  if (records === null || records.length === 0) {
    return (
      <Box sx={fillSx}>
        <Typography variant="body2">No records found.</Typography>
      </Box>
    );
  }

  function makeTable(data: Record<string, unknown>) {
    const rows = [];
    Object.keys(data).forEach((key) => {
      const cells = [];
      let cellData;
      if (data[key] === undefined || data[key] === null) {
        // return here is inside the forEach, so it's effectively a continue
        return;
      }
      if (Array.isArray(data[key])) {
        // Unique the list prior to iterating over it.
        const uniqueItems = [...new Set(data[key] as unknown[])];
        const listItems = [];
        uniqueItems.forEach((item, index) => {
          let mungedItem;
          if (typeof item === 'object' && item !== null) {
            mungedItem = makeTable(item as Record<string, unknown>);
          } else {
            mungedItem = String(item);
          }
          listItems.push(
            // eslint-disable-next-line  @eslint-react/no-array-index-key
            <ListItem disableGutters key={`${key}-${index}`}>
              {mungedItem}
            </ListItem>,
          );
          if (uniqueItems.length !== 1) {
            listItems.push(
              // eslint-disable-next-line  @eslint-react/no-array-index-key
              <Divider key={`${key}-${index}-divider`} component="li" />,
            );
          }
        });
        cellData = (
          <List disablePadding dense sx={{ mt: -0.5 }}>
            {listItems}
          </List>
        );
      } else if (typeof data[key] === 'object') {
        cellData = makeTable(data[key] as Record<string, unknown>);
      } else {
        const item = data[key];
        cellData = <Typography variant="body1">{String(item)}</Typography>;
      }
      cells.push(
        <TableCell
          width="250px"
          style={{ borderBottom: 'none', verticalAlign: 'top' }}
          key={key}
        >
          {key}
        </TableCell>,
      );
      cells.push(
        <TableCell
          width="100%"
          style={{ borderBottom: 'none', verticalAlign: 'top' }}
          key={`${key}-data`}
        >
          {cellData}
        </TableCell>,
      );
      rows.push(
        <TableRow style={{ borderTop: '1px' }} key={key}>
          {cells}
        </TableRow>,
      );
    });
    return (
      <Table sx={{ maxWidth: '100%', m: -0.7, ml: -1.9 }} size="small">
        <TableBody>{rows}</TableBody>
      </Table>
    );
  }

  const tables = [];
  for (let i = 0; i < records.length; i++) {
    const record = records[i];
    const mungedData = flattenRecord(record);
    const caption = String(mungedData[id]);
    const table = makeTable(mungedData);
    tables.push(
      <Box
        key={i}
        sx={{ position: 'relative', '&:hover .panel-info-btn': { opacity: 1 } }}
      >
        <IconButton
          className="panel-info-btn"
          size="small"
          onClick={handleClickOpen}
          sx={{
            position: 'absolute',
            top: 0,
            right: 0,
            opacity: 0,
            transition: 'opacity 0.2s',
          }}
        >
          <Info fontSize="small" />
        </IconButton>
        <Typography gutterBottom variant="h4" component="div">
          {caption}
          <QueryValidationBadge errors={queryErrors} warnings={warnings} />
        </Typography>
        <Divider />
        <TableContainer component={Paper} sx={{ p: 2 }}>
          {table}
        </TableContainer>
      </Box>,
    );
  }

  return (
    <Box sx={fillSx}>
      <Box sx={autoHeight ? autoBodySx : scrollBodySx}>{tables}</Box>
      <CypherDetails details={details} open={open} setOpen={setOpen} />
    </Box>
  );
}
