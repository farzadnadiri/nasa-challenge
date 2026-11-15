import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { ErrorBoundary } from './components/ErrorBoundary'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary fallback={
      <div className="app"><div className="error" role="alert">
        Something went wrong while showing this view. <button onClick={() => location.reload()}>Reload</button>
      </div></div>}>
      <App />
    </ErrorBoundary>
  </StrictMode>,
)
