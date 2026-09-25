import { Outlet, useSearchParams } from 'react-router-dom';
import { RunsSidebar } from './RunsSidebar';

// Layout route: the sidebar is mounted once and is identical on / and /upload,
// so "New assessment" swaps only the main panel.
export function AppShell() {
  const [searchParams] = useSearchParams();
  const selectedRunId = searchParams.get('run');

  return (
    <div className="flex h-full">
      <RunsSidebar selectedRunId={selectedRunId} />
      <main className="min-w-0 flex-1 overflow-y-auto">
        <Outlet />
      </main>
    </div>
  );
}
