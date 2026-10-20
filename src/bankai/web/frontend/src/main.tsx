import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { Toaster } from 'sonner';
import App from './App';
import './index.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
      <Toaster
        theme='dark'
        position='bottom-right'
        toastOptions={{
          style: {
            background: 'oklch(0.115 0 0)',
            border: '1px solid oklch(0.235 0 0)',
            color: 'oklch(0.985 0 0)',
            boxShadow: '0 16px 40px -24px oklch(0 0 0 / 0.85)',
          },
        }}
      />
    </BrowserRouter>
  </React.StrictMode>,
);
