"use client";

import { use } from "react";
import { useSearchParams } from "next/navigation";
import { ItemReview } from "@/components/ItemReview";
import { useSession } from "@/lib/session";

export default function ItemPage({ params }: {
  params: Promise<{ id: string; itemId: string }>;
}) {
  const { id, itemId } = use(params);
  const search = useSearchParams();
  const { user, role } = useSession();
  const stockroomId = search.get("stockroom_id") ?? undefined;
  return <ItemReview key={JSON.stringify([id, itemId, stockroomId, user, role])}
    batchId={Number(id)} itemId={itemId} stockroomId={stockroomId} />;
}
