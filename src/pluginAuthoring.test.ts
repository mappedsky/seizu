import type { ToolParamDef } from 'src/hooks/useToolsetsApi';
import {
  PLUGIN_SCHEMA,
  SEIZU_EXTENSION,
  declaredToolName,
  isExternalDeclaration,
  parseManifest,
  parseSkillDocument,
  seizuExtension,
  serializeManifest,
  serializeSkillDocument,
  toolDeclaration,
  validateSkillAuthoring,
} from 'src/pluginAuthoring';

describe('plugin authoring documents', () => {
  it('round-trips the supported SKILL.md front matter and body', () => {
    const source = `---
name: review-repository
description: Review a repository for security issues
allowed-tools: graph__query mcp:github/search_code
---
# Review

Inspect {% $repository %}.`;

    const parsed = parseSkillDocument(source);

    expect(parsed).toEqual({
      portableName: 'review-repository',
      description: 'Review a repository for security issues',
      allowedTools: ['graph__query', 'mcp:github/search_code'],
      body: '# Review\n\nInspect {% $repository %}.',
      metadata: {
        name: 'review-repository',
        description: 'Review a repository for security issues',
        'allowed-tools': 'graph__query mcp:github/search_code',
      },
    });
    expect(parseSkillDocument(serializeSkillDocument(parsed))).toEqual(parsed);
  });

  it('preserves unrelated plugin manifest fields and extensions', () => {
    const manifest = parseManifest(
      JSON.stringify({
        $schema: PLUGIN_SCHEMA,
        name: 'security-review',
        xVendorField: true,
        extensions: {
          'org.example.vendor': { setting: 'kept' },
          [SEIZU_EXTENSION]: {
            skills: {},
          },
        },
      }),
    );

    seizuExtension(manifest).skills.review = { title: 'Review' };
    const serialized = JSON.parse(serializeManifest(manifest));

    expect(serialized.xVendorField).toBe(true);
    expect(serialized.extensions['org.example.vendor']).toEqual({
      setting: 'kept',
    });
    expect(serialized.extensions[SEIZU_EXTENSION].skills.review).toEqual({
      title: 'Review',
    });
  });

  it('validates placeholders against the declared inputs', () => {
    const parameters: ToolParamDef[] = [
      {
        name: 'repository',
        type: 'string',
        description: '',
        required: true,
        default: null,
      },
    ];
    const skill = {
      portableName: 'review-repository',
      description: 'Review a repository',
      allowedTools: [],
      body: 'Review {% $repository %} and {% $branch %}.',
    };

    expect(validateSkillAuthoring(skill, 'review_repository', parameters)).toBe(
      'Variable $branch must match a declared input.',
    );
    expect(
      validateSkillAuthoring(
        { ...skill, body: 'Review {% $repository %}.' },
        'review_repository',
        parameters,
      ),
    ).toBeNull();
  });
});

describe('allowed-tools declarations', () => {
  // A package names every dependency mcp__<server>__<tool>, Seizu's own
  // included. A bare group__action is read back as the consuming client's own
  // built-in, so the dependency is dropped and the skill renders with no tools
  // disclosed. Mirrors allowed_tool_entry in plugin_packages.py.
  it.each([
    ['graph__query', 'mcp__seizu__graph__query'],
    ['skillsets__create_skill', 'mcp__seizu__skillsets__create_skill'],
    ['cve_analysis__get_cve', 'mcp__seizu__cve_analysis__get_cve'],
    ['ext__github__search_code', 'mcp__github__search_code'],
  ])('declares the catalog name %s as %s', (mcpName, expected) => {
    expect(toolDeclaration(mcpName)).toBe(expected);
    expect(declaredToolName(expected)).toBe(mcpName);
  });

  it.each([
    'mcp__seizu__graph__query',
    'mcp__github__search_code',
    'Read',
    'WebSearch',
    'Bash(git:*)',
  ])('leaves %s unchanged', (entry) => {
    expect(toolDeclaration(entry)).toBe(entry);
    expect(toolDeclaration(toolDeclaration(entry))).toBe(entry);
  });

  it('round-trips an entry it does not own', () => {
    expect(toolDeclaration(declaredToolName('Read'))).toBe('Read');
  });

  it('flags only a dependency that needs an mcp.json server entry', () => {
    expect(isExternalDeclaration('mcp__github__search_code')).toBe(true);
    expect(isExternalDeclaration('mcp__seizu__graph__query')).toBe(false);
    expect(isExternalDeclaration('Read')).toBe(false);
  });
});
