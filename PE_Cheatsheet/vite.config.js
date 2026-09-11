import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// './' keeps asset URLs relative, so the built site works from a GitHub Pages
// project subpath (user.github.io/repo/) without hardcoding the repo name,
// and also works if you just open dist/index.html locally.
export default defineConfig({
  plugins: [react()],
  base: './',
})
