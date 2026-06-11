export interface PanelParam {
  name: string;
  value?: string;
  input_id?: string;
}

export interface Panel {
  type: string;
  cypher?: string;
  details_cypher?: string;
  caption?: string;
  hide_caption?: boolean;
  w?: number;
  h?: number;
  x?: number;
  y?: number;
  min_h?: number;
  auto_height?: boolean;
  threshold?: number;
  thresholds?: PanelThreshold[];
  params?: PanelParam[];
  pie_settings?: PieSettings;
  bar_settings?: BarSettings;
  graph_settings?: GraphSettings;
  progress_settings?: ProgressSettings;
  table_id?: string;
  legend?: string;
  markdown?: string;
  columns?: ColumnDef[];
}

export interface PieSettings {
  legend?: string;
}

export interface BarSettings {
  legend?: string;
}

export interface GraphSettings {
  node_label?: string;
  node_color_by?: string;
}

export interface ProgressSettings {
  show_label?: boolean;
}

export interface PanelThreshold {
  value: number;
  color: string;
}

export interface ColumnDef {
  name: string;
  label: string;
}

export interface Row {
  name: string;
  hide_header?: boolean;
  collapsible?: boolean;
  panels: Panel[];
}

export interface ReportInput {
  input_id: string;
  type: string;
  cypher?: string;
  params?: Record<string, string>;
  label: string;
  default?: InputValue;
  size?: number;
}

export interface InputValue {
  label: string;
  value: string;
}

export interface Report {
  schema_version?: number;
  name?: string;
  queries?: Record<string, string>;
  rows: Row[];
  inputs?: ReportInput[];
}

export interface ActionConfigFieldDef {
  name: string;
  label: string;
  type: 'string' | 'text' | 'number' | 'boolean' | 'string_list' | 'select';
  required?: boolean;
  description?: string;
  default?: unknown;
  options?: string[];
  warning?: string;
}

export interface SeizuConfig {
  scheduled_query_action_types: string[];
  scheduled_query_action_schemas: Record<string, ActionConfigFieldDef[]>;
  // Action types that require a permission to configure (e.g. agent_chat ->
  // chat:bypass_permissions); the UI hides them from users who lack it.
  scheduled_query_action_permissions?: Record<string, string>;
}
