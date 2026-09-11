# PE Cheatsheet

Petroleum/reservoir engineering quick-reference site. Vite + React, deployed to GitHub Pages.

## First-time setup

```bash
git init
git branch -M main
npm install
git add .
git commit -m "Initial scaffold: Vite + React + GitHub Pages workflow"
```

Then create an empty repo on GitHub and:

```bash
git remote add origin https://github.com/<you>/pe-cheatsheet.git
git push -u origin main
```

In the GitHub repo: **Settings → Pages → Source → GitHub Actions**. The workflow
in `.github/workflows/deploy.yml` builds and publishes on every push to `main`.

## Day to day

```bash
npm run dev      # local dev server, hot reload
npm run build    # production build into dist/
npm run preview  # serve the built dist/ locally
```

## Layout

```
index.html                     entry HTML
vite.config.js                 base: './' so Pages subpaths work
src/main.jsx                   React root
src/App.jsx                    top-level component
src/index.css                  global styles (light + dark)
.github/workflows/deploy.yml   Pages deploy
```

## Note on OneDrive

This folder is inside OneDrive. `node_modules/` and `dist/` are gitignored but
OneDrive will still try to sync them, which is slow and occasionally locks files
mid-install. Consider excluding this folder from OneDrive sync (right-click →
"Always keep on this device" off / or Settings → Sync and backup → exclude), or
moving the repo to a non-synced path like `C:\dev\pe-cheatsheet`.
