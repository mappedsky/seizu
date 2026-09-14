import { render } from '@testing-library/react';
import PageTitle from 'src/components/PageTitle';

describe('PageTitle', () => {
  it('names the tab while it is mounted', () => {
    render(<PageTitle>Spaces | Seizu</PageTitle>);
    expect(document.title).toBe('Spaces | Seizu');
  });

  it('assembles a title interpolated from values', () => {
    const name = 'CVE report';
    render(<PageTitle>History – {name} | Seizu</PageTitle>);
    expect(document.title).toBe('History – CVE report | Seizu');
  });

  it('follows the value it was given', () => {
    const { rerender } = render(<PageTitle>First | Seizu</PageTitle>);
    rerender(<PageTitle>Second | Seizu</PageTitle>);
    expect(document.title).toBe('Second | Seizu');
  });

  it('gives the tab up on unmount, so a page that sets no title does not inherit one', () => {
    const { unmount } = render(<PageTitle>Spaces | Seizu</PageTitle>);
    expect(document.title).toBe('Spaces | Seizu');
    unmount();
    expect(document.title).not.toBe('Spaces | Seizu');
  });

  it('renders nothing into the page', () => {
    const { container } = render(<PageTitle>Spaces | Seizu</PageTitle>);
    expect(container).toBeEmptyDOMElement();
  });
});
