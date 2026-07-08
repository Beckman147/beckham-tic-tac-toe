# Tic Tac Toe

A clean, single-player Tic Tac Toe game — you vs. the computer. Built with plain
HTML, CSS, and JavaScript (no frameworks, no build step) and hosted for free on
GitHub Pages.

**Play it here:** `https://<your-username>.github.io/beckham-tic-tac-toe/`
*(replace `<your-username>` with your GitHub username once deployed)*

## Features

- **Four difficulty levels**, powered by the [minimax algorithm](https://en.wikipedia.org/wiki/Minimax):
  - **Easy** — the computer moves randomly.
  - **Medium** — plays the optimal move about half the time.
  - **Hard** — plays the optimal move about 75% of the time.
  - **God Mode** — plays perfectly every move. Mathematically unbeatable; the best you can do is draw.
- Choose to play as **X or O** (X always moves first).
- **Scoreboard** for wins / draws / losses, saved in your browser (`localStorage`) so it survives refreshes.
- Responsive design that works on phone and desktop, with automatic light/dark theme.

## Run locally

No tooling required — just open `index.html` in any browser.

Or, to serve it over `http://` (closer to production):

```bash
# Python 3
python -m http.server 8000
# then visit http://localhost:8000
```

## Project structure

| File | Purpose |
|------|---------|
| `index.html` | Markup and layout |
| `styles.css` | Styling, theming, animations |
| `script.js`  | Game logic + minimax AI |
| `favicon.svg`| Browser tab icon |

## Deploy to GitHub Pages

1. Create a **public** GitHub repository (e.g. `beckham-tic-tac-toe`) and push this code to the `main` branch.
2. In the repo, go to **Settings → Pages**.
3. Under **Build and deployment**, set **Source** to **Deploy from a branch**, pick branch **`main`** and folder **`/ (root)`**, then **Save**.
4. Wait ~1 minute. Your game is live (with automatic HTTPS) at
   `https://<your-username>.github.io/beckham-tic-tac-toe/`.

Any push to `main` afterward redeploys automatically.
