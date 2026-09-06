import { useState } from 'react';
import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  Paper,
  Tab,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableRow,
  Tabs,
  Typography,
} from '@mui/material';
import useMediaQuery from '@mui/material/useMediaQuery';
import { useTheme } from '@mui/material/styles';

import CypherTable from 'src/components/reports/CypherTable';

interface TabPanelProps {
  children?: React.ReactNode;
  index: number;
  value: number;
}

function TabPanel({ children, value, index, ...other }: TabPanelProps) {
  return (
    <div
      role="tabpanel"
      hidden={value !== index}
      id={`simple-tabpanel-${index}`}
      aria-labelledby={`simple-tab-${index}`}
      {...other}
    >
      {value === index && <Box sx={{ p: 3 }}>{children}</Box>}
    </div>
  );
}

function a11yProps(index: number) {
  return {
    id: `simple-tab-${index}`,
    'aria-controls': `simple-tabpanel-${index}`,
  };
}

interface DetailsData {
  params?: Record<string, unknown>;
  type?: string;
  caption?: string;
  cypher?: string;
  details_cypher?: string;
  columns?: Array<{ name: string; label: string }>;
  reportQueryToken?: string;
  detailsQueryToken?: string;
  [key: string]: unknown;
}

interface CypherDetailsProps {
  details?: DetailsData;
  open: boolean;
  setOpen: (open: boolean) => void;
}

export default function CypherDetails({
  details = {},
  open,
  setOpen,
}: CypherDetailsProps) {
  const theme = useTheme();
  const fullScreen = useMediaQuery(theme.breakpoints.down('sm'));
  const handleClose = () => {
    setOpen(false);
  };

  const [value, setValue] = useState(0);
  const handleChange = (_event: React.SyntheticEvent, newValue: number) => {
    setValue(newValue);
  };

  let params;
  if (details.params !== undefined) {
    params = Object.keys(details.params).map((key) => {
      let paramKey = details.params![key];
      if (Array.isArray(details.params![key])) {
        paramKey = (details.params![key] as unknown[]).join(', ');
      }
      return (
        <span key={key} style={{ display: 'block' }}>
          {key}: {String(paramKey)}
        </span>
      );
    });
  }

  let cypherRow;
  if (details.cypher !== undefined && details.cypher !== null) {
    cypherRow = (
      <TableRow>
        <TableCell>
          <Typography variant="body1">Cypher Query</Typography>
        </TableCell>
        <TableCell>
          <Typography variant="body1" style={{ whiteSpace: 'pre-line' }}>
            {details.cypher}
          </Typography>
        </TableCell>
      </TableRow>
    );
  }

  let detailsCypherRow;
  if (details.details_cypher !== undefined && details.details_cypher !== null) {
    detailsCypherRow = (
      <TableRow>
        <TableCell>
          <Typography variant="body1">Data Details Cypher Query</Typography>
        </TableCell>
        <TableCell>
          <Typography variant="body1" style={{ whiteSpace: 'pre-line' }}>
            {details.details_cypher}
          </Typography>
        </TableCell>
      </TableRow>
    );
  }

  let paramsRow;
  if (params !== undefined && params.length !== 0) {
    paramsRow = (
      <TableRow>
        <TableCell>
          <Typography variant="body1">Query Parameters</Typography>
        </TableCell>
        <TableCell>
          <Typography variant="body1" component="div">
            {params}
          </Typography>
        </TableCell>
      </TableRow>
    );
  }

  let cypherTabs;
  let cypherTabPanel;
  if (details.details_cypher === undefined || details.details_cypher === null) {
    cypherTabs = [<Tab key={0} label="Query Details" {...a11yProps(0)} />];
    cypherTabPanel = (
      <TabPanel value={value} index={0}>
        <TableContainer component={Paper}>
          <Table aria-label="extra-details">
            <TableBody>
              {cypherRow}
              {detailsCypherRow}
              {paramsRow}
            </TableBody>
          </Table>
        </TableContainer>
      </TabPanel>
    );
  } else {
    cypherTabs = [
      <Tab key={0} label="Data Details" {...a11yProps(0)} />,
      <Tab key={1} label="Query Details" {...a11yProps(1)} />,
    ];
    cypherTabPanel = (
      <>
        <TabPanel value={value} index={0}>
          <Typography gutterBottom variant="h4">
            Details for: {details.caption}
          </Typography>
          <CypherTable
            cypher={details.details_cypher}
            params={details.params}
            columns={details.columns}
            height="450px"
            reportQueryToken={details.detailsQueryToken}
          />
        </TabPanel>
        <TabPanel value={value} index={1}>
          <TableContainer component={Paper}>
            <Table aria-label="extra-details">
              <TableBody>
                {cypherRow}
                {detailsCypherRow}
                {paramsRow}
              </TableBody>
            </Table>
          </TableContainer>
        </TabPanel>
      </>
    );
  }

  return (
    <Dialog
      fullScreen={fullScreen}
      open={open}
      onClose={handleClose}
      aria-labelledby="responsive-dialog-title"
      maxWidth="xl"
      fullWidth
    >
      <DialogContent>
        <Box sx={{ width: '100%', height: '790px' }}>
          <Box sx={{ borderBottom: 1, borderColor: 'divider' }}>
            <Tabs
              value={value}
              onChange={handleChange}
              aria-label="Query details tabs"
            >
              {cypherTabs}
            </Tabs>
          </Box>
          {cypherTabPanel}
        </Box>
      </DialogContent>
      <DialogActions>
        <Button onClick={handleClose} color="primary" autoFocus>
          Close
        </Button>
      </DialogActions>
    </Dialog>
  );
}
