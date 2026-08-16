import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import './theme.css';
import './site/theme.css';
import { SiteApp } from './site/SiteApp';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <SiteApp />
  </StrictMode>,
);
