import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import './theme.css';
import './site/theme.css';
import { SiteApp } from './site/SiteApp';
import { initializeTheme } from './ui/theme';

initializeTheme();

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <SiteApp />
  </StrictMode>,
);
