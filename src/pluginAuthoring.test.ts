import type { ToolParamDef } from 'src/hooks/useToolsetsApi';
import {
  PLUGIN_SCHEMA,
  SEIZU_EXTENSION,
  parseManifest,
  parseSkillDocument,
  seizuExtension,
  serializeManifest,
  serializeSkillDocument,
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
