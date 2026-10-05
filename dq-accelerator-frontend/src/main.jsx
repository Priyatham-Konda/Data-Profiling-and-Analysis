import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import './styles/index.css';

async function enableMocking() {
  if (!import.meta.env.DEV) return;

  // Setting VITE_API_BASE_URL means a real backend exists -- use it. Without
  // this check, MSW's handlers are built from that same API_BASE (see
  // api/client.js), so it would just start matching the real backend's URL
  // instead and keep answering with fake data; the real server would never
  // see a single request.
  if (import.meta.env.VITE_API_BASE_URL) return;

  const { worker } = await import('./mocks/browser');
  // Anything we didn't write a handler for passes straight through to the network.
  await worker.start({ onUnhandledRequest: 'bypass' });
}

// Await the worker before rendering, otherwise the first queries race the
// service worker registration and fall through to a 404.
enableMocking().then(() => {
  createRoot(document.getElementById('root')).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
});
