/**
 * The `$parameter` names a Cypher query references, in first-use order.
 *
 * Comments and string literals are skipped, so `'$5'` or `// $todo` is not a
 * parameter. Backtick-quoted names (`` $`my param` ``) are returned unquoted.
 */
export function cypherParameterNames(cypher: string): string[] {
  const names: string[] = [];
  const seen = new Set<string>();
  const add = (name: string) => {
    if (name && !seen.has(name)) {
      seen.add(name);
      names.push(name);
    }
  };
  let i = 0;
  while (i < cypher.length) {
    const ch = cypher[i];
    const next = cypher[i + 1];
    if (ch === '/' && next === '/') {
      const end = cypher.indexOf('\n', i);
      i = end === -1 ? cypher.length : end + 1;
    } else if (ch === '/' && next === '*') {
      const end = cypher.indexOf('*/', i + 2);
      i = end === -1 ? cypher.length : end + 2;
    } else if (ch === "'" || ch === '"') {
      i += 1;
      while (i < cypher.length && cypher[i] !== ch) {
        i += cypher[i] === '\\' ? 2 : 1;
      }
      i += 1;
    } else if (ch === '$' && next === '`') {
      const end = cypher.indexOf('`', i + 2);
      if (end === -1) break;
      add(cypher.slice(i + 2, end));
      i = end + 1;
    } else if (ch === '$') {
      const match = /^[A-Za-z_][A-Za-z0-9_]*/.exec(cypher.slice(i + 1));
      if (match) add(match[0]);
      i += 1 + (match?.[0].length ?? 0);
    } else {
      i += 1;
    }
  }
  return names;
}
