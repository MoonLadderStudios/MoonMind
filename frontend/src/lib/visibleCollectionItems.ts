import { createContext, useContext, useEffect } from 'react';

export type VisibleCollection = 'workflow' | 'recurring' | 'skill';

/**
 * Receives the ids a collection's full table shows, in display order after its
 * filters and sort, or null while the table has no settled rows. The dashboard
 * shell owns it and uses the ids to choose the "first item" when the operator
 * leaves the table for a detail mode, so it opens what the operator saw.
 */
export type VisibleCollectionItemsRegistry = {
  publish: (collection: VisibleCollection, ids: readonly string[] | null) => void;
};

export const VisibleCollectionItemsContext = createContext<VisibleCollectionItemsRegistry | null>(null);

/** Publish a table's visible ids (memoize `ids`); a page outside the shell publishes nothing. */
export function usePublishVisibleCollectionItems(
  collection: VisibleCollection,
  ids: readonly string[] | null,
): void {
  const registry = useContext(VisibleCollectionItemsContext);
  useEffect(() => {
    if (!registry) {
      return undefined;
    }
    registry.publish(collection, ids);
    return () => registry.publish(collection, null);
  }, [collection, ids, registry]);
}

/**
 * The remembered item when the visible collection still contains it, otherwise
 * the first visible item. A remembered item outside the visible collection is
 * dropped rather than opened.
 */
export function chooseVisibleItem(
  visibleIds: readonly string[],
  rememberedId: string | null | undefined,
): string | null {
  const remembered = rememberedId?.trim();
  if (remembered && visibleIds.includes(remembered)) {
    return remembered;
  }
  return visibleIds[0] ?? null;
}
