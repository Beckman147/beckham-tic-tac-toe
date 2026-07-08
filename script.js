/* ============================================================
   Tic Tac Toe — single player vs. computer
   Vanilla JS. AI uses the minimax algorithm.
   ============================================================ */

"use strict";

/* ---------- Constants ---------- */
const WIN_LINES = [
  [0, 1, 2], [3, 4, 5], [6, 7, 8], // rows
  [0, 3, 6], [1, 4, 7], [2, 5, 8], // columns
  [0, 4, 8], [2, 4, 6],            // diagonals
];

// Probability the computer plays the optimal (minimax) move per difficulty.
const OPTIMAL_CHANCE = { easy: 0, medium: 0.5, hard: 0.75, god: 1 };

const STORAGE_SCORES = "ttt-scores";
const STORAGE_SETTINGS = "ttt-settings";

/* ---------- DOM refs ---------- */
const boardEl = document.getElementById("board");
const statusEl = document.getElementById("status");
const difficultyEl = document.getElementById("difficulty");
const sideButtons = Array.from(document.querySelectorAll(".segmented__btn"));
const newGameBtn = document.getElementById("new-game");
const resetScoreBtn = document.getElementById("reset-score");
const scoreEls = {
  wins: document.getElementById("score-wins"),
  draws: document.getElementById("score-draws"),
  losses: document.getElementById("score-losses"),
};

/* ---------- Persisted state ---------- */
const settings = loadSettings();       // { difficulty, humanMark }
let scores = loadScores();             // { wins, draws, losses }

/* ---------- Game state ---------- */
let board = Array(9).fill("");
let humanMark = settings.humanMark;    // "X" or "O"
let aiMark = other(humanMark);
let turn = "X";                        // X always moves first
let gameOver = false;

/* ============================================================
   Helpers
   ============================================================ */
function other(mark) {
  return mark === "X" ? "O" : "X";
}

function emptyIndices(b) {
  const out = [];
  for (let i = 0; i < 9; i++) if (b[i] === "") out.push(i);
  return out;
}

// Returns the winning line ([a,b,c]) for the given board, or null.
function winningLine(b) {
  for (const line of WIN_LINES) {
    const [a, c, d] = line;
    if (b[a] && b[a] === b[c] && b[a] === b[d]) return line;
  }
  return null;
}

/* ============================================================
   Minimax AI
   Returns { score, index } for the best move for `player`.
   Depth is baked into the score so the AI prefers quick wins
   and slow losses.
   ============================================================ */
function minimax(b, player, depth) {
  const line = winningLine(b);
  if (line) {
    const winner = b[line[0]];
    return { score: winner === aiMark ? 10 - depth : depth - 10, index: -1 };
  }
  const avail = emptyIndices(b);
  if (avail.length === 0) return { score: 0, index: -1 };

  let best = {
    score: player === aiMark ? -Infinity : Infinity,
    index: avail[0],
  };

  for (const idx of avail) {
    b[idx] = player;
    const result = minimax(b, other(player), depth + 1);
    b[idx] = "";

    if (player === aiMark) {
      if (result.score > best.score) best = { score: result.score, index: idx };
    } else {
      if (result.score < best.score) best = { score: result.score, index: idx };
    }
  }
  return best;
}

// Choose the computer's move based on the selected difficulty.
function chooseComputerMove() {
  const avail = emptyIndices(board);
  const playOptimal = Math.random() < (OPTIMAL_CHANCE[difficultyEl.value] ?? 1);

  if (playOptimal) {
    return minimax(board.slice(), aiMark, 0).index;
  }
  return avail[Math.floor(Math.random() * avail.length)];
}

/* ============================================================
   Rendering
   ============================================================ */
function buildBoard() {
  boardEl.innerHTML = "";
  for (let i = 0; i < 9; i++) {
    const cell = document.createElement("button");
    cell.className = "cell";
    cell.type = "button";
    cell.dataset.index = String(i);
    cell.setAttribute("role", "gridcell");
    cell.setAttribute("aria-label", `Cell ${i + 1}, empty`);
    cell.addEventListener("click", () => handleCellClick(i));
    boardEl.appendChild(cell);
  }
}

function renderBoard() {
  const cells = boardEl.children;
  for (let i = 0; i < 9; i++) {
    const cell = cells[i];
    const mark = board[i];
    const already = cell.textContent === mark;
    cell.textContent = mark;
    cell.disabled = mark !== "" || gameOver || turn !== humanMark;
    if (mark) {
      cell.dataset.mark = mark;
      cell.setAttribute("aria-label", `Cell ${i + 1}, ${mark}`);
      if (!already) cell.classList.add("is-filled");
    } else {
      delete cell.dataset.mark;
      cell.setAttribute("aria-label", `Cell ${i + 1}, empty`);
      cell.classList.remove("is-filled");
    }
    cell.classList.remove("is-win");
  }
}

function highlightWin(line) {
  for (const i of line) boardEl.children[i].classList.add("is-win");
}

function renderScores() {
  scoreEls.wins.textContent = scores.wins;
  scoreEls.draws.textContent = scores.draws;
  scoreEls.losses.textContent = scores.losses;
}

function setStatus(text, tone) {
  statusEl.textContent = text;
  statusEl.classList.toggle("is-win", tone === "win");
  statusEl.classList.toggle("is-lose", tone === "lose");
}

function updateTurnStatus() {
  if (turn === humanMark) setStatus(`Your turn (${humanMark})`);
  else setStatus("Computer is thinking…");
}

/* ============================================================
   Game flow
   ============================================================ */
function startNewGame() {
  board = Array(9).fill("");
  gameOver = false;
  turn = "X";
  renderBoard();
  updateTurnStatus();

  // If the human chose O, the computer (X) moves first.
  if (turn === aiMark) {
    scheduleComputerMove();
  }
}

function handleCellClick(i) {
  if (gameOver || board[i] !== "" || turn !== humanMark) return;
  placeMark(i, humanMark);
  if (!gameOver && turn === aiMark) scheduleComputerMove();
}

function scheduleComputerMove() {
  renderBoard(); // disables cells while the computer "thinks"
  updateTurnStatus();
  setTimeout(() => {
    if (gameOver) return;
    const idx = chooseComputerMove();
    if (idx != null && idx >= 0) placeMark(idx, aiMark);
  }, 350);
}

function placeMark(i, mark) {
  board[i] = mark;
  turn = other(mark);
  renderBoard();

  const line = winningLine(board);
  if (line) {
    endGame(board[line[0]], line);
  } else if (emptyIndices(board).length === 0) {
    endGame(null, null);
  } else {
    updateTurnStatus();
  }
}

function endGame(winner, line) {
  gameOver = true;
  renderBoard(); // disable everything

  if (winner === humanMark) {
    scores.wins++;
    setStatus("You win! 🎉", "win");
  } else if (winner === aiMark) {
    scores.losses++;
    setStatus("Computer wins.", "lose");
  } else {
    scores.draws++;
    setStatus("It's a draw.");
  }
  if (line) highlightWin(line);

  saveScores();
  renderScores();
}

/* ============================================================
   Settings & side selection
   ============================================================ */
function selectSide(mark) {
  humanMark = mark;
  aiMark = other(mark);
  sideButtons.forEach((btn) => {
    const active = btn.dataset.side === mark;
    btn.classList.toggle("is-active", active);
    btn.setAttribute("aria-checked", String(active));
  });
  saveSettings();
  startNewGame();
}

/* ============================================================
   Persistence (localStorage, with safe fallbacks)
   ============================================================ */
function loadScores() {
  try {
    const raw = JSON.parse(localStorage.getItem(STORAGE_SCORES));
    if (raw && typeof raw === "object") {
      return {
        wins: raw.wins | 0,
        draws: raw.draws | 0,
        losses: raw.losses | 0,
      };
    }
  } catch (_) { /* ignore */ }
  return { wins: 0, draws: 0, losses: 0 };
}

function saveScores() {
  try { localStorage.setItem(STORAGE_SCORES, JSON.stringify(scores)); } catch (_) {}
}

function loadSettings() {
  const defaults = { difficulty: "hard", humanMark: "X" };
  try {
    const raw = JSON.parse(localStorage.getItem(STORAGE_SETTINGS));
    if (raw && typeof raw === "object") {
      return {
        difficulty: OPTIMAL_CHANCE.hasOwnProperty(raw.difficulty) ? raw.difficulty : defaults.difficulty,
        humanMark: raw.humanMark === "O" ? "O" : "X",
      };
    }
  } catch (_) { /* ignore */ }
  return defaults;
}

function saveSettings() {
  try {
    localStorage.setItem(
      STORAGE_SETTINGS,
      JSON.stringify({ difficulty: difficultyEl.value, humanMark })
    );
  } catch (_) {}
}

/* ============================================================
   Event wiring
   ============================================================ */
difficultyEl.addEventListener("change", () => {
  saveSettings();
  startNewGame();
});

sideButtons.forEach((btn) => {
  btn.addEventListener("click", () => selectSide(btn.dataset.side));
});

newGameBtn.addEventListener("click", startNewGame);

resetScoreBtn.addEventListener("click", () => {
  scores = { wins: 0, draws: 0, losses: 0 };
  saveScores();
  renderScores();
});

/* ============================================================
   Init
   ============================================================ */
difficultyEl.value = settings.difficulty;
buildBoard();
renderScores();
selectSide(settings.humanMark); // syncs UI and starts the first game
