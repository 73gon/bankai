import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import App from './App';
import { Toaster } from '@/zon/ui/toast';
import { TooltipProvider } from '@/zon/ui/tooltip';
import { bakeGround } from '@/lib/ground';
import './index.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <TooltipProvider>
        <App />
      </TooltipProvider>
      <Toaster />
    </BrowserRouter>
  </React.StrictMode>,
);

// The page ground the scrolling area paints (lib/ground.ts): until it is baked, that area is plain --background.
bakeGround().catch(() => undefined);
