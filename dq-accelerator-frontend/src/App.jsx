import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { AppShell } from '@/components/AppShell';
import { Toast, ToastProvider } from '@/components/Toast';
import { RunsProvider } from '@/components/RunsProvider';
import { HomePanel } from '@/pages/HomePanel';
import { UploadPanel } from '@/pages/UploadPanel';

export default function App() {
  return (
    // Nesting order matters: RunsProvider reads the toast context, so it has to
    // live inside ToastProvider.
    <ToastProvider>
      <RunsProvider>
        <BrowserRouter>
          <Routes>
            <Route element={<AppShell />}>
              <Route path="/" element={<HomePanel />} />
              <Route path="/upload" element={<UploadPanel />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Route>
          </Routes>
        </BrowserRouter>
        {/* Outside the router on purpose: navigation can never unmount it. */}
        <Toast />
      </RunsProvider>
    </ToastProvider>
  );
}
