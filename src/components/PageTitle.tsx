import { Children, ReactNode, useEffect } from 'react';

/**
 * What `index.html` declared, captured before React has rendered anything.
 * Restored when no page is claiming a title, so navigating to one of the pages
 * that sets none leaves the tab saying "Seizu" rather than the previous page's
 * title.
 */
const DEFAULT_TITLE = typeof document === 'undefined' ? '' : document.title;

/** One entry per mounted `PageTitle`; the tab reverts when the last one goes. */
const claims = new Set<object>();

/** Children render to text; anything else (an element, null) contributes nothing. */
function textOf(children: ReactNode): string {
  return Children.toArray(children)
    .map((child) =>
      typeof child === 'string' || typeof child === 'number'
        ? String(child)
        : '',
    )
    .join('');
}

/**
 * Sets `document.title` for as long as it is mounted.
 *
 * **Exactly one may be mounted at a time.** A title belongs to the page, and
 * two simultaneous claims have no defined winner — whichever effect runs last
 * takes the tab, which is an ordering React does not promise to keep stable.
 * Rendering a second one is a bug, and says so in development rather than
 * silently showing the wrong title. A page that embeds a component which sets
 * its own title (`ReportView`) must therefore not also set one.
 */
function PageTitle({ children }: { children: ReactNode }) {
  const title = textOf(children);

  useEffect(() => {
    const claim = {};
    claims.add(claim);
    if (import.meta.env.DEV && claims.size > 1) {
      console.warn(
        `Two PageTitle components are mounted at once ("${document.title}" and ` +
          `"${title}"). The tab shows whichever rendered last; give exactly one ` +
          `owner the title.`,
      );
    }
    document.title = title;
    return () => {
      claims.delete(claim);
      if (claims.size === 0) document.title = DEFAULT_TITLE;
    };
  }, [title]);

  return null;
}

export default PageTitle;
