export type CatalogPageSize = 10 | 25 | 50 | 100;

export const CATALOG_PAGE_SIZES: CatalogPageSize[] = [10, 25, 50, 100];
const PAGE_SIZE_KEY = 'bankai:catalog-page-size';

export function loadCatalogPageSize(): CatalogPageSize {
  try {
    const value = Number(localStorage.getItem(PAGE_SIZE_KEY));
    if (CATALOG_PAGE_SIZES.includes(value as CatalogPageSize)) return value as CatalogPageSize;
  } catch {
    // Storage can be unavailable in private/locked-down browser contexts.
  }
  return 50;
}

export function saveCatalogPageSize(value: CatalogPageSize): void {
  try {
    localStorage.setItem(PAGE_SIZE_KEY, String(value));
  } catch {
    // The in-memory preference still works for this session.
  }
}
