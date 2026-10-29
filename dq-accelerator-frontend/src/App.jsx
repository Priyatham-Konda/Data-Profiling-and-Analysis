import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { AppShell } from '@/components/AppShell';
import { AuthProvider, RequireAuth } from '@/components/AuthProvider';
import { Toast, ToastProvider } from '@/components/Toast';
import { AuthPage } from '@/pages/AuthPage';
import { HomePanel } from '@/pages/HomePanel';
import { UploadPanel } from '@/pages/UploadPanel';
import { SalesforcePanel } from '@/pages/SalesforcePanel';
import { DashboardPanel } from '@/pages/DashboardPanel';

export default function App() {
  return (
    // RunsProvider reads the toast context, so ToastProvider stays outermost.
    // RunsProvider itself is mounted by RequireAuth, inside the router: it
    // must not exist, or poll, until someone has signed in.
    <ToastProvider>
      <AuthProvider>
        <BrowserRouter>
          <Routes>
            <Route path="/login" element={<AuthPage mode="login" />} />
            <Route path="/signup" element={<AuthPage mode="signup" />} />
            <Route element={<RequireAuth />}>
              <Route element={<AppShell />}>
                <Route path="/" element={<HomePanel />} />
                <Route path="/upload" element={<UploadPanel />} />
                <Route path="/salesforce" element={<SalesforcePanel />} />
                <Route path="/dashboard" element={<DashboardPanel />} />
                <Route path="*" element={<Navigate to="/" replace />} />
              </Route>
            </Route>
          </Routes>
        </BrowserRouter>
      </AuthProvider>
      {/* Outside the router on purpose: navigation can never unmount it. */}
      <Toast />
    </ToastProvider>
  );
}
