import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { GoogleOAuthProvider } from '@react-oauth/google'
import './index.css'
import App from './App.tsx'
import { getConfig } from './lib/api.ts'

getConfig()
  .then(({ google_client_id }) => {
    createRoot(document.getElementById('root')!).render(
      <StrictMode>
        <GoogleOAuthProvider clientId={google_client_id}>
          <App />
        </GoogleOAuthProvider>
      </StrictMode>,
    )
  })
  .catch(() => {
    // Config fetch failed — render without Google OAuth
    createRoot(document.getElementById('root')!).render(
      <StrictMode>
        <GoogleOAuthProvider clientId="">
          <App />
        </GoogleOAuthProvider>
      </StrictMode>,
    )
  })
