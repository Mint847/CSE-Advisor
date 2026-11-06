import { StrictMode, useEffect } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import { startSession, useSession } from './auth/session'
import SignedOut, { StartingScreen } from './pages/SignIn'
import '@fontsource/ibm-plex-mono/latin-400.css'
import '@fontsource/ibm-plex-mono/latin-500.css'
import '@fontsource/ibm-plex-mono/latin-600.css'
import './index.css'

/** Three states only: starting (a neutral screen, never a flash of the log-in page), signed out, or the app. */
function Root() {
  const { status } = useSession()
  useEffect(() => { void startSession() }, [])
  if (status === 'starting') return <StartingScreen />
  if (status === 'signedOut') return <SignedOut />
  return <App />
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <Root />
    </BrowserRouter>
  </StrictMode>,
)
