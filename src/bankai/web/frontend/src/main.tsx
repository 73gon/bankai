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
            background: 'hsl(255 22% 9%)',
            border: '1px solid hsl(268 18% 18%)',
            color: 'hsl(270 20% 96%)',
          },
        }}
      />
    </BrowserRouter>
  </React.StrictMode>,
);
