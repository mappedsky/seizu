import YAML from 'yaml';
import type { ToolParamDef } from 'src/hooks/useToolsetsApi';

export const PLUGIN_SCHEMA =
  'https://agent-plugins.org/schemas/1.0.0/plugin.schema.json';
export const SEIZU_EXTENSION = 'com.mappedsky.seizu';
export const PORTABLE_SKILL_NAME = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
export const LOWER_SNAKE_ID = /^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$/;
export const MARKDOC_VAR_RE = /\{%\s*\$([a-z][a-z0-9_]*)\s*%\}/g;
export const MAX_SLUG_LEN = 31;

/**
 * The Seizu id a portable name implies, or null when it implies none.
 *
 * One name, one id: a package or skill that wants a different id renames
 * itself. Mirrors `derive_seizu_id` in reporting/services/plugin_packages.py.
 */
export function deriveSeizuId(portableName: string): string | null {
  const candidate = portableName.replaceAll('-', '_').replaceAll('.', '_');
  if (!LOWER_SNAKE_ID.test(candidate) || candidate.length > MAX_SLUG_LEN) {
    return null;
  }
  return candidate;
}

export interface PluginAuthor {
  name?: string;
  email?: string;
  url?: string;
}

export interface PluginSkillExtension {
  skillId?: string;
  title?: string;
  enabled?: boolean;
  triggers?: string[];
  parameters?: ToolParamDef[];
  aliases?: string[];
}

export interface SeizuPluginExtension {
  skillsetId: string;
  skills: Record<string, PluginSkillExtension>;
  legacySkillsetProjection?: boolean;
}

export interface PluginManifest {
  $schema: string;
  name: string;
  version?: string;
  description?: string;
  author?: PluginAuthor;
  homepage?: string;
  repository?: string;
  license?: string;
  keywords?: string[];
  extensions?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface SkillDocument {
  portableName: string;
  description: string;
  allowedTools: string[];
  body: string;
  metadata?: Record<string, unknown>;
}

export function parseManifest(text: string): PluginManifest {
  const value = JSON.parse(text) as PluginManifest;
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('plugin.json must contain a JSON object.');
  }
  if (!value.extensions || typeof value.extensions !== 'object') {
    value.extensions = {};
  }
  return value;
}

export function seizuExtension(manifest: PluginManifest): SeizuPluginExtension {
  const extensions = (manifest.extensions ??= {});
  const existing = extensions[SEIZU_EXTENSION];
  if (existing && typeof existing === 'object' && !Array.isArray(existing)) {
    const extension = existing as unknown as SeizuPluginExtension;
    extension.skills ??= {};
    return extension;
  }
  const extension: SeizuPluginExtension = { skillsetId: '', skills: {} };
  extensions[SEIZU_EXTENSION] = extension;
  return extension;
}

export function serializeManifest(manifest: PluginManifest): string {
  return `${JSON.stringify(manifest, null, 2)}\n`;
}

export function parseSkillDocument(text: string): SkillDocument {
  if (!text.startsWith('---\n')) {
    throw new Error('SKILL.md must begin with YAML front matter.');
  }
  const close = text.indexOf('\n---', 4);
  if (close < 0)
    throw new Error('SKILL.md has unterminated YAML front matter.');
  const metadata = YAML.parse(text.slice(4, close)) as Record<string, unknown>;
  if (!metadata || typeof metadata !== 'object' || Array.isArray(metadata)) {
    throw new Error('SKILL.md front matter must be an object.');
  }
  const name = metadata.name;
  const description = metadata.description;
  const allowedTools = metadata['allowed-tools'] ?? '';
  if (typeof name !== 'string' || typeof description !== 'string') {
    throw new Error('SKILL.md requires string name and description fields.');
  }
  if (typeof allowedTools !== 'string') {
    throw new Error('SKILL.md allowed-tools must be a space-separated string.');
  }
  return {
    portableName: name,
    description,
    allowedTools: allowedTools.split(/\s+/).filter(Boolean),
    body: text.slice(close + 4).replace(/^\r?\n/, ''),
    metadata,
  };
}

export function serializeSkillDocument(skill: SkillDocument): string {
  const metadata: Record<string, unknown> = {
    ...(skill.metadata ?? {}),
    name: skill.portableName,
    description: skill.description,
  };
  if (skill.allowedTools.length) {
    metadata['allowed-tools'] = skill.allowedTools.join(' ');
  } else {
    delete metadata['allowed-tools'];
  }
  return `---\n${YAML.stringify(metadata).trimEnd()}\n---\n${skill.body}`;
}

export function validateSkillAuthoring(
  skill: SkillDocument,
  skillId: string,
  parameters: ToolParamDef[],
): string | null {
  if (!PORTABLE_SKILL_NAME.test(skill.portableName)) {
    return 'Portable name must use lowercase words separated by hyphens.';
  }
  if (!LOWER_SNAKE_ID.test(skillId) || skillId.length > 31) {
    return 'Skill ID must be lower_snake_case and at most 31 characters.';
  }
  if (!skill.description.trim() || skill.description.length > 1024) {
    return 'Description must be between 1 and 1024 characters.';
  }
  if (!skill.body.trim()) return 'Instructions are required.';

  const names = new Set<string>();
  for (const parameter of parameters) {
    if (!LOWER_SNAKE_ID.test(parameter.name) || names.has(parameter.name)) {
      return 'Parameter names must be unique lower_snake_case values.';
    }
    names.add(parameter.name);
  }
  // Substitution still renders, so an undeclared placeholder is still an
  // error -- but a body that carries none is the shape to author for: the
  // values arrive in a rendered Inputs block, keeping SKILL.md portable.
  for (const match of skill.body.matchAll(MARKDOC_VAR_RE)) {
    if (!names.has(match[1])) {
      return `Variable $${match[1]} must match a declared input.`;
    }
  }
  return null;
}
