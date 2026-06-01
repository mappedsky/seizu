import { render, screen, cleanup } from '@testing-library/react';
import fc from 'fast-check';
import { MarkdocRenderer } from '../renderer';

afterEach(cleanup);

describe('MarkdocRenderer', () => {
  it('substitutes variables from the variables prop', () => {
    render(
      <MarkdocRenderer
        source="Hello {% $name %}"
        variables={{ name: 'world' }}
      />,
    );
    expect(screen.getByText(/Hello world/)).toBeInTheDocument();
  });

  it('treats undefined variables as empty strings', () => {
    const { container } = render(
      <MarkdocRenderer source="Hello {% $missing %}!" variables={{}} />,
    );
    expect(container.textContent).toContain('Hello !');
  });

  it('treats absent variables as falsy in {% if not($foo) %}', () => {
    const source = '{% if not($foo) %}unset{% else /%}set{% /if %}';
    const { container, rerender } = render(
      <MarkdocRenderer source={source} variables={{}} />,
    );
    expect(container.textContent).toContain('unset');
    rerender(<MarkdocRenderer source={source} variables={{ foo: 'bar' }} />);
    expect(container.textContent).toContain('set');
  });

  it('demotes h1 in markdown source to h2 in output', () => {
    render(<MarkdocRenderer source={'# A heading'} />);
    const heading = screen.getByText('A heading');
    expect(heading.tagName).toBe('H2');
  });

  it('renders tables wrapped in a TableContainer', () => {
    const md = '| h1 | h2 |\n|----|----|\n| a  | b  |\n';
    const { container } = render(<MarkdocRenderer source={md} />);
    expect(container.querySelector('table')).not.toBeNull();
    expect(container.textContent).toContain('h1');
    expect(container.textContent).toContain('a');
  });

  it('applies the mui-markdown-ol class to ordered lists', () => {
    const { container } = render(<MarkdocRenderer source={'1. one\n2. two'} />);
    const ol = container.querySelector('ol');
    expect(ol).not.toBeNull();
    expect(ol?.className).toContain('mui-markdown-ol');
  });

  it('applies the mui-markdown-ul class to bullet lists', () => {
    const { container } = render(<MarkdocRenderer source={'- one\n- two'} />);
    const ul = container.querySelector('ul');
    expect(ul).not.toBeNull();
    expect(ul?.className).toContain('mui-markdown-ul');
  });

  it('substitutes variables inside link href (compact form)', () => {
    const { container } = render(
      <MarkdocRenderer
        source="[label](https://github.com/{%$org%})"
        variables={{ org: 'octo-org' }}
      />,
    );
    const a = container.querySelector('a');
    expect(a).not.toBeNull();
    expect(a?.getAttribute('href')).toBe('https://github.com/octo-org');
    expect(a?.textContent).toBe('label');
  });

  it('renders links with the themed MUI Link component', () => {
    const { container } = render(
      <MarkdocRenderer source="[home](https://example.com)" />,
    );
    const a = container.querySelector('a');
    expect(a).not.toBeNull();
    expect(a?.className).toContain('MuiLink-root');
    expect(a?.className).toContain('MuiLink-underlineHover');
  });

  it('does not render nested paragraphs when conditionals return block content', () => {
    const { container } = render(
      <MarkdocRenderer
        source={'Before {% if $show %}\nShown\n{% /if %}'}
        variables={{ show: 'yes' }}
      />,
    );
    expect(container.querySelector('p p')).toBeNull();
    expect(container.textContent).toContain('Before');
    expect(container.textContent).toContain('Shown');
  });

  it('substitutes variables inside angle-bracketed link href (spaced form)', () => {
    const { container } = render(
      <MarkdocRenderer
        source="[label](<https://github.com/{% $org %}>)"
        variables={{ org: 'octo-org' }}
      />,
    );
    const a = container.querySelector('a');
    expect(a).not.toBeNull();
    expect(a?.getAttribute('href')).toBe('https://github.com/octo-org');
  });

  it('substitutes variables inside image src', () => {
    const { container } = render(
      <MarkdocRenderer
        source="![alt](https://cdn.example.com/{%$file%}.png)"
        variables={{ file: 'logo' }}
      />,
    );
    const img = container.querySelector('img');
    expect(img).not.toBeNull();
    expect(img?.getAttribute('src')).toBe('https://cdn.example.com/logo.png');
  });

  it('keeps a substituted URL with an allowlisted scheme', () => {
    const { container } = render(
      <MarkdocRenderer
        source="[click](https://example.com/{%$path%})"
        variables={{ path: 'reports/42' }}
      />,
    );
    expect(container.querySelector('a')?.getAttribute('href')).toBe(
      'https://example.com/reports/42',
    );
  });

  it('blocks raw javascript: protocol from a fully variable href', () => {
    const { container } = render(
      <MarkdocRenderer
        source="[click](<{%$url%}>)"
        variables={{ url: 'javascript:alert(1)' }}
      />,
    );
    expect(container.querySelector('a')?.getAttribute('href')).toBe('#');
  });

  it('blocks javascript: with an embedded tab (browsers strip tabs from href)', () => {
    const { container } = render(
      <MarkdocRenderer
        source="[click](<{%$url%}>)"
        variables={{ url: 'java\tscript:alert(1)' }}
      />,
    );
    expect(container.querySelector('a')?.getAttribute('href')).toBe('#');
  });

  it('blocks javascript: with an embedded newline', () => {
    const { container } = render(
      <MarkdocRenderer
        source="[click](<{%$url%}>)"
        variables={{ url: 'java\nscript:alert(1)' }}
      />,
    );
    expect(container.querySelector('a')?.getAttribute('href')).toBe('#');
  });

  it('blocks javascript: with a leading null byte', () => {
    const { container } = render(
      <MarkdocRenderer
        source="[click](<{%$url%}>)"
        variables={{ url: '\u0000javascript:alert(1)' }}
      />,
    );
    expect(container.querySelector('a')?.getAttribute('href')).toBe('#');
  });

  it('blocks mixed-case Javascript: after substitution', () => {
    const { container } = render(
      <MarkdocRenderer
        source="[click](<{%$url%}>)"
        variables={{ url: 'JaVaScRiPt:alert(1)' }}
      />,
    );
    expect(container.querySelector('a')?.getAttribute('href')).toBe('#');
  });

  it('strips control characters from a substituted https URL before rendering', () => {
    const { container } = render(
      <MarkdocRenderer
        source="[home](<{%$url%}>)"
        variables={{ url: 'https://example.com/p\tath' }}
      />,
    );
    expect(container.querySelector('a')?.getAttribute('href')).toBe(
      'https://example.com/path',
    );
  });

  it('blocks custom OS protocol handlers from a variable-built href', () => {
    const { container } = render(
      <MarkdocRenderer
        source="[click](<{%$url%}>)"
        variables={{ url: 'slack://channel/T01' }}
      />,
    );
    expect(container.querySelector('a')?.getAttribute('href')).toBe('#');
  });

  it('blocks protocol-relative URLs from a variable-built href', () => {
    const { container } = render(
      <MarkdocRenderer
        source="[click](<{%$url%}>)"
        variables={{ url: '//evil.example.com/x' }}
      />,
    );
    expect(container.querySelector('a')?.getAttribute('href')).toBe('#');
  });

  it('allows mailto and tel after substitution', () => {
    const { container } = render(
      <>
        <MarkdocRenderer
          source="[email](mailto:{%$addr%})"
          variables={{ addr: 'a@b.co' }}
        />
        <MarkdocRenderer
          source="[call](tel:{%$num%})"
          variables={{ num: '+15555555' }}
        />
      </>,
    );
    const links = container.querySelectorAll('a');
    expect(links[0]?.getAttribute('href')).toBe('mailto:a@b.co');
    expect(links[1]?.getAttribute('href')).toBe('tel:+15555555');
  });

  it('preserves static editor-authored URLs unchanged (no substitution check)', () => {
    // A static slack:// URL from an editor passes through markdown-it's parser
    // and our renderer does not second-guess it. This is intentional: the
    // allowlist only applies when a variable substitution changed the URL.
    const { container } = render(
      <MarkdocRenderer source="[chat](slack://channel/T01)" variables={{}} />,
    );
    expect(container.querySelector('a')?.getAttribute('href')).toBe(
      'slack://channel/T01',
    );
  });

  it('blocks non-browser static link schemes in untrusted URL mode', () => {
    const { container } = render(
      <MarkdocRenderer source="[chat](slack://channel/T01)" untrustedUrls />,
    );
    expect(container.querySelector('a')?.getAttribute('href')).toBe('#');
  });

  it('blocks protocol-relative static links in untrusted URL mode', () => {
    const { container } = render(
      <MarkdocRenderer source="[chat](//evil.example.com/x)" untrustedUrls />,
    );
    expect(container.querySelector('a')?.getAttribute('href')).toBe('#');
  });

  it('allows https static links in untrusted URL mode', () => {
    const { container } = render(
      <MarkdocRenderer
        source="[home](https://example.com/path)"
        untrustedUrls
      />,
    );
    expect(container.querySelector('a')?.getAttribute('href')).toBe(
      'https://example.com/path',
    );
  });

  it('blocks non-http static image URLs in untrusted URL mode', () => {
    const { container } = render(
      <MarkdocRenderer
        source="![pixel](data:image/png;base64,AAAA)"
        untrustedUrls
      />,
    );
    expect(container.querySelector('img')?.getAttribute('src')).toBe('#');
  });

  it('preserves non-substituted link URLs unchanged', () => {
    const { container } = render(
      <MarkdocRenderer
        source="[home](https://example.com/path)"
        variables={{}}
      />,
    );
    expect(container.querySelector('a')?.getAttribute('href')).toBe(
      'https://example.com/path',
    );
  });

  it('fuzzes markdown fragments without rendering raw executable HTML', () => {
    const markdownFragment = fc.oneof(
      fc.string({ minLength: 0, maxLength: 80 }),
      fc.constantFrom(
        '# Heading',
        '## Subheading',
        '**bold** and _italic_',
        '- one\n- two',
        '1. one\n2. two',
        '| h | v |\n|---|---|\n| a | b |',
        '[home](https://example.com/{%$path%})',
        '![alt](https://example.com/{%$image%}.png)',
        '<script>alert(1)</script>',
        '<img src=x onerror=alert(1)>',
        '{% if $show %}visible{% /if %}',
      ),
    );

    fc.assert(
      fc.property(
        fc.array(markdownFragment, { minLength: 1, maxLength: 8 }),
        fc.string({ minLength: 0, maxLength: 40 }),
        fc.string({ minLength: 0, maxLength: 40 }),
        (fragments, path, image) => {
          const { container } = render(
            <MarkdocRenderer
              source={fragments.join('\n\n')}
              variables={{ path, image, show: 'true' }}
            />,
          );

          try {
            expect(container.querySelector('script')).toBeNull();
            expect(container.querySelector('[onerror]')).toBeNull();
            expect(container.querySelector('[onclick]')).toBeNull();
          } finally {
            cleanup();
          }
        },
      ),
      { numRuns: 75 },
    );
  });

  it('fuzzes variable-built javascript hrefs and keeps them blocked', () => {
    const javascriptProtocol = fc
      .constant('javascript:')
      .chain((protocol) =>
        fc.tuple(
          ...protocol
            .split('')
            .map((char) =>
              fc
                .tuple(
                  fc.boolean(),
                  fc.constantFrom('', '\u0000', '\t', '\n', '\r'),
                )
                .map(
                  ([upper, separator]) =>
                    `${upper ? char.toUpperCase() : char}${separator}`,
                ),
            ),
        ),
      )
      .map((parts) => `${parts.join('')}alert(1)`);

    fc.assert(
      fc.property(javascriptProtocol, (url) => {
        const { container } = render(
          <MarkdocRenderer source="[click](<{%$url%}>)" variables={{ url }} />,
        );

        try {
          expect(container.querySelector('a')?.getAttribute('href')).toBe('#');
        } finally {
          cleanup();
        }
      }),
      { numRuns: 75 },
    );
  });
});
