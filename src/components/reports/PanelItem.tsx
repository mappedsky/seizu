import { memo } from 'react';
import { Box } from '@mui/material';

import { MarkdocRenderer } from 'src/components/markdoc/renderer';
import { Panel, ReportInput } from 'src/config.context';
import CypherBar from 'src/components/reports/CypherBar';
import CypherCount from 'src/components/reports/CypherCount';
import CypherGraph from 'src/components/reports/CypherGraph';
import CypherPie from 'src/components/reports/CypherPie';
import CypherProgress from 'src/components/reports/CypherProgress';
import CypherTable from 'src/components/reports/CypherTable';
import CypherVerticalTable from 'src/components/reports/CypherVerticalTable';

export interface PanelItemProps {
  item: Panel;
  rowIndex: number;
  index: number;
  varData: Record<string, { label?: string; value?: string }>;
  allInputs: ReportInput[];
  resolveQuery: (cypher: string | undefined) => string | undefined;
  resolveCapability: (path: string) => string | undefined;
  refreshKey: number;
  onTokenExpired: () => void;
}

const PanelItem = memo(
  function PanelItem({
    item,
    rowIndex,
    index,
    varData,
    allInputs,
    resolveQuery,
    resolveCapability,
    refreshKey,
    onTokenExpired,
  }: PanelItemProps) {
    const needInputs: string[] = [];
    const params: Record<string, string | undefined> = {};
    if (item.params !== undefined) {
      item.params.forEach((inputData) => {
        const paramName = inputData.name;
        const paramValue = inputData?.value;
        const paramInputId = inputData?.input_id;
        if (paramValue != null) {
          params[paramName] = paramValue;
        } else if (paramInputId != null) {
          params[paramName] = varData[paramInputId]?.value;
          if (
            params[paramName] === undefined ||
            params[paramName] === null ||
            params[paramName] === ''
          ) {
            try {
              const input = allInputs.find(
                (obj) => obj.input_id === paramInputId,
              );
              needInputs.push(input!.label);
            } catch (err) {
              console.log(err);
              needInputs.push(`*(Error: undefined input: ${paramInputId})`);
            }
          }
        }
      });
    }

    const effectiveCaption = item.hide_caption ? undefined : item.caption;

    const details = {
      cypher: resolveQuery(item.cypher),
      details_cypher: resolveQuery(item.details_cypher),
      type: item.type,
      columns: item.columns,
      caption: effectiveCaption,
      params,
      reportQueryToken: resolveCapability(
        `rows.${rowIndex}.panels.${index}.cypher`,
      ),
      detailsQueryToken: resolveCapability(
        `rows.${rowIndex}.panels.${index}.details_cypher`,
      ),
    };

    let itemComponent;
    if (item.type === 'progress') {
      itemComponent = (
        <CypherProgress
          cypher={resolveQuery(item.cypher)}
          params={params}
          caption={effectiveCaption}
          threshold={item.threshold}
          thresholds={item.thresholds}
          progressSettings={item.progress_settings}
          details={details}
          needInputs={needInputs}
          reportQueryToken={resolveCapability(
            `rows.${rowIndex}.panels.${index}.cypher`,
          )}
          refreshKey={refreshKey}
          onTokenExpired={onTokenExpired}
        />
      );
    } else if (item.type === 'pie') {
      itemComponent = (
        <CypherPie
          cypher={resolveQuery(item.cypher)}
          params={params}
          caption={effectiveCaption}
          pieSettings={item.pie_settings}
          details={details}
          needInputs={needInputs}
          reportQueryToken={resolveCapability(
            `rows.${rowIndex}.panels.${index}.cypher`,
          )}
          refreshKey={refreshKey}
          onTokenExpired={onTokenExpired}
        />
      );
    } else if (item.type === 'bar') {
      itemComponent = (
        <CypherBar
          cypher={resolveQuery(item.cypher)}
          params={params}
          caption={effectiveCaption}
          barSettings={item.bar_settings}
          details={details}
          needInputs={needInputs}
          reportQueryToken={resolveCapability(
            `rows.${rowIndex}.panels.${index}.cypher`,
          )}
          refreshKey={refreshKey}
          onTokenExpired={onTokenExpired}
        />
      );
    } else if (item.type === 'graph') {
      itemComponent = (
        <CypherGraph
          cypher={resolveQuery(item.cypher)}
          params={params}
          caption={effectiveCaption}
          graphSettings={item.graph_settings}
          needInputs={needInputs}
          fillHeight
          reportQueryToken={resolveCapability(
            `rows.${rowIndex}.panels.${index}.cypher`,
          )}
          refreshKey={refreshKey}
          onTokenExpired={onTokenExpired}
        />
      );
    } else if (item.type === 'count') {
      itemComponent = (
        <CypherCount
          cypher={resolveQuery(item.cypher)}
          params={params}
          caption={effectiveCaption}
          threshold={item.threshold}
          thresholds={item.thresholds}
          details={details}
          needInputs={needInputs}
          reportQueryToken={resolveCapability(
            `rows.${rowIndex}.panels.${index}.cypher`,
          )}
          refreshKey={refreshKey}
          onTokenExpired={onTokenExpired}
        />
      );
    } else if (item.type === 'table') {
      itemComponent = (
        <CypherTable
          cypher={resolveQuery(item.cypher)}
          params={params}
          columns={item.columns}
          caption={effectiveCaption}
          details={details}
          needInputs={needInputs}
          reportQueryToken={resolveCapability(
            `rows.${rowIndex}.panels.${index}.cypher`,
          )}
          refreshKey={refreshKey}
          onTokenExpired={onTokenExpired}
        />
      );
    } else if (item.type === 'vertical-table') {
      itemComponent = (
        <CypherVerticalTable
          cypher={resolveQuery(item.cypher)}
          params={params}
          id={item.table_id}
          details={details}
          needInputs={needInputs}
          autoHeight={item.auto_height ?? false}
          reportQueryToken={resolveCapability(
            `rows.${rowIndex}.panels.${index}.cypher`,
          )}
          refreshKey={refreshKey}
          onTokenExpired={onTokenExpired}
        />
      );
    } else if (item.type === 'markdown') {
      // Markdoc's truthy check treats '' and 0 as truthy; only undefined/null/false are falsy.
      // Omit unset/empty values so {% if not($foo) %} works when an input is cleared.
      const flatVars: Record<string, string> = {};
      allInputs.forEach((input) => {
        const value = varData[input.input_id]?.value;
        if (value !== undefined && value !== '') {
          flatVars[input.input_id] = value;
        }
      });
      itemComponent = (
        <Box
          sx={{
            ...(item.auto_height
              ? { height: 'auto' }
              : { height: '100%', minHeight: 0, overflow: 'auto' }),
            '& p': { mb: 1 },
            '& h2, & h3, & h4, & h5, & h6': { mb: 1 },
            '& ul, & ol': { mb: 1 },
            '& hr': { my: 2 },
            '& li:has(> input[type="checkbox"])': {
              listStyle: 'none',
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              ml: '-1.5em',
              '& p': { my: 0 },
            },
          }}
        >
          <MarkdocRenderer source={item.markdown ?? ''} variables={flatVars} />
        </Box>
      );
    }

    // ``auto_height`` panels render at content height; the parent grid grows
    // the cell to match. Other panels flex-fill their assigned cell.
    if (item.auto_height) {
      return <Box sx={{ width: '100%' }}>{itemComponent}</Box>;
    }
    return (
      <Box
        sx={{
          height: '100%',
          minHeight: 0,
          display: 'flex',
          flexDirection: 'column',
        }}
      >
        {itemComponent}
      </Box>
    );
  },
  function areEqual(prevProps, nextProps) {
    // refreshKey drives explicit re-renders (user refresh, token recovery after expiry).
    // Capability identity changes alone do not trigger re-renders; refreshKey handles that.
    if (prevProps.refreshKey !== nextProps.refreshKey) return false;
    if (prevProps.resolveQuery !== nextProps.resolveQuery) return false;
    if (prevProps.onTokenExpired !== nextProps.onTokenExpired) return false;
    if (prevProps.rowIndex !== nextProps.rowIndex) return false;
    if (prevProps.index !== nextProps.index) return false;
    if (prevProps.item !== nextProps.item) return false;

    // Markdown panels can reference any input via {% $id %} — check all inputs.
    if (nextProps.item.type === 'markdown') {
      for (const input of nextProps.allInputs) {
        if (
          prevProps.varData[input.input_id]?.value !==
          nextProps.varData[input.input_id]?.value
        )
          return false;
      }
      return true;
    }

    // Only re-render if a varData value for an input this panel uses has changed
    const inputIds = (nextProps.item.params ?? [])
      .map((p) => p.input_id)
      .filter((id): id is string => id != null);

    for (const id of inputIds) {
      if (prevProps.varData[id]?.value !== nextProps.varData[id]?.value)
        return false;
    }
    return true;
  },
);

export default PanelItem;
