import type { SVGProps } from 'react';

/**
 * bankai's mark: the zon crescent (a 12 disc at 16,16 less a 9.312 circle) cut by one diagonal,
 * its horns and foot slid 0.7 apart along the cut. Sharp corners on purpose. The viewBox is
 * cropped to the moon, so the height is the size; drawn in currentColor. Same as favicon.svg.
 */
export function BankaiMark(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox='3 3 26 26' fill='currentColor' xmlns='http://www.w3.org/2000/svg' {...props}>
      <path d='M26.447 11.693A12 12 0 0 0 15.565 4.282A9.312 9.312 0 0 1 23.958 12.781L26.447 11.693Z' />
      <path d='M9.543 5.784A12 12 0 0 0 4.464 21.31L7.814 19.844A9.312 9.312 0 0 1 9.543 5.784Z' />
      <path d='M6.682 22.414A12 12 0 0 0 28.317 12.948L25.177 14.322A9.312 9.312 0 0 1 10.683 20.663L6.682 22.414Z' />
    </svg>
  );
}
