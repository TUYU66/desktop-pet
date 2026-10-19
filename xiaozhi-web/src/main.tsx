import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import './styles/notion-theme.css'
import './styles/workspace-motion.css'
import './styles/layout-refinements.css'
import './styles/interaction-motion.css'
import App from './App.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
