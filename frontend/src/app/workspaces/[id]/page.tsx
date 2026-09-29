import { WorkspaceDetail } from "@/components/Workspaces";

export default async function WorkspacePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <WorkspaceDetail key={id} id={id} />;
}
