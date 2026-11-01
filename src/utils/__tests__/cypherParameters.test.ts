import { cypherParameterNames } from 'src/utils/cypherParameters';

describe('cypherParameterNames', () => {
  it('returns each parameter once, in first-use order', () => {
    expect(
      cypherParameterNames(
        'MATCH (c:CVE) WHERE c.severity = $severity AND c.score > $min ' +
          'RETURN c LIMIT $limit // $severity again: $severity',
      ),
    ).toEqual(['severity', 'min', 'limit']);
  });

  it('skips string literals and comments', () => {
    expect(
      cypherParameterNames(
        "RETURN '$price', \"it's $5\" /* $hidden */ // $todo\n, $real",
      ),
    ).toEqual(['real']);
  });

  it('unquotes backtick-quoted names', () => {
    expect(cypherParameterNames('RETURN $`my param`, $plain')).toEqual([
      'my param',
      'plain',
    ]);
  });

  it('ignores a bare dollar sign', () => {
    expect(cypherParameterNames('RETURN $ + 1')).toEqual([]);
  });
});
