import { CardSkeleton, Skeleton } from "@/components/ui";

/**
 * Route-level fallback for one item.
 *
 * The queue's "Open" button is the most-pressed control in the console. This
 * makes the press visible while the segment loads; the page's own skeleton
 * takes over for the five requests that follow.
 */
export default function Loading() {
  return (
    <div className="page-wide flex flex-col gap-5" role="status" aria-busy="true">
      <span className="sr-only">Loading item…</span>

      <div className="skeleton-stack">
        <Skeleton w="220px" h="1.35rem" />
        <Skeleton w="52%" h="0.8rem" />
      </div>

      <div className="grid gap-5 lg:grid-cols-[1.1fr_1fr]">
        <div className="flex flex-col gap-5">
          <CardSkeleton lines={5} />
          <CardSkeleton lines={3} />
          <CardSkeleton lines={4} />
        </div>
        <div className="flex flex-col gap-5">
          <CardSkeleton lines={6} />
          <CardSkeleton lines={4} />
        </div>
      </div>
    </div>
  );
}
