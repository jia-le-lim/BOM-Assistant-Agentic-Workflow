import { CardSkeleton, Skeleton, TableSkeleton } from "@/components/ui";

/**
 * Route-level fallback for /batches/[id].
 *
 * Opening a batch from the list loads this segment's chunk before any of the
 * page's four requests even start. Without a boundary the previous page just
 * sits there and the click reads as ignored, so the shape of the batch page
 * appears immediately instead.
 */
export default function Loading() {
  return (
    <div className="page-wide flex flex-col gap-6" role="status" aria-busy="true">
      <span className="sr-only">Loading batch…</span>

      <div className="skeleton-stack">
        <Skeleton w="240px" h="1.35rem" />
        <Skeleton w="46%" h="0.8rem" />
      </div>

      <div className="grid gap-4 grid-cols-2 lg:grid-cols-4">
        {Array.from({ length: 4 }, (_, i) => <CardSkeleton key={i} lines={2} />)}
      </div>

      <div className="work-split">
        <div className="card p-5">
          <Skeleton w="30%" h="0.85rem" />
          <div className="mt-4">
            <TableSkeleton rows={8} cols={7} label="Loading the review queue" />
          </div>
        </div>
        <div className="work-rail">
          <CardSkeleton lines={3} />
          <CardSkeleton lines={5} />
        </div>
      </div>
    </div>
  );
}
