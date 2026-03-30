const subtitle = document.getElementById("subtitle");
const balanceEl = document.getElementById("balance");
const boardEl = document.getElementById("board");
const pickBoxEl = document.getElementById("pickBox");
const statusHintEl = document.getElementById("statusHint");
const playStatusHintEl = document.getElementById("playStatusHint");
const myCardSection = document.getElementById("myCardSection");
const myCardEl = document.getElementById("myCard");
const bingoHeadEl = document.getElementById("bingoHead");
const claimBingoBtn = document.getElementById("claimBingoBtn");
const winnerModal = document.getElementById("winnerModal");
const winnerCloseBtn = document.getElementById("winnerCloseBtn");
const winnerHeadTitle = document.getElementById("winnerHeadTitle");
const winnerPrizeAmt = document.getElementById("winnerPrizeAmt");
const winnerCardWrap = document.getElementById("winnerCardWrap");
const winnerFooterBot = document.getElementById("winnerFooterBot");

const statsRow = document.getElementById("statsRow");
const currentCallBig = document.getElementById("currentCallBig");
const recentCallsEl = document.getElementById("recentCalls");
const masterBingo = document.getElementById("masterBingo");
const spectatorHero = document.getElementById("spectatorHero");
const spectatorHeroLead = document.getElementById("spectatorHeroLead");
const spectatorHeroHint = document.getElementById("spectatorHeroHint");
const spectatorEyeIcon = document.getElementById("spectatorEyeIcon");
const disqualifiedFalseBingoXIcon = document.getElementById("disqualifiedFalseBingoXIcon");
const viewSelect = document.getElementById("viewSelect");
const viewPlay = document.getElementById("viewPlay");
const ticketLegendLobby = document.getElementById("ticketLegendLobby");
const pickBoardLabel = document.getElementById("pickBoardLabel");
const lobbyControls = document.getElementById("lobbyControls");
const lobbyTicketSection = document.getElementById("lobbyTicketSection");
const liveCallCard = document.getElementById("liveCallCard");
const sideStarted = document.getElementById("sideStarted");
const lobbyStrip = document.getElementById("lobbyStrip");
const appHeader = document.querySelector("#app > header.header");
const headerActions = document.querySelector("#app > header.header .headerActions");
const userAvatar = document.getElementById("userAvatar");
const topCard = document.getElementById("topCard");
const topWallet = document.getElementById("topWallet");
const topStake = document.getElementById("topStake");
const topStarting = document.getElementById("topStarting");
const balanceBanner = document.getElementById("balanceBanner");
const balanceBannerText = document.getElementById("balanceBannerText");
const themeToggle = document.getElementById("themeToggle");
const lobbyCardPreview = document.getElementById("lobbyCardPreview");
const lobbyPreviewLetters = document.getElementById("lobbyPreviewLetters");
const lobbyPreviewGrid = document.getElementById("lobbyPreviewGrid");
const boardTransitionOverlay = document.getElementById("boardTransitionOverlay");

/** Shown in browser tab; some Telegram builds also mirror this in the Mini App header. */
const MINI_APP_DISPLAY_NAME = "ETHIO BINGO";

function applyMiniAppDisplayTitle() {
  document.title = MINI_APP_DISPLAY_NAME;
  requestAnimationFrame(() => {
    document.title = MINI_APP_DISPLAY_NAME;
  });
  window.setTimeout(() => {
    document.title = MINI_APP_DISPLAY_NAME;
  }, 0);
  window.setTimeout(() => {
    document.title = MINI_APP_DISPLAY_NAME;
  }, 250);
}

applyMiniAppDisplayTitle();

let selectedPick = null;
let lastGameId = null;
let initData = null;
let startParam = null;
let lastWinnerModalGameId = null;
/** Finished `game_id` whose winner card we already showed (avoids duplicate after BINGO claim + lobby poll). */
let lastPreviousRoundWinnerShownId = null;
let lastBalanceEtb = 0;
/** Last `/games/active` payload — drives lobby UI and balance banner. */
let lastGameForUi = null;
let securingPick = false;
/** Absolute deadline (ms) for lobby card pick — updated each `/games/active` response. */
let anchoredLobbyDeadlineMs = NaN;
let anchoredLobbyGameId = null;
/** For playing-board entrance when status goes lobby → running (pick window closed). */
let prevViewStatusForTransition = null;
let rushLobbyPollTimer = null;
/** Avoid stacking rush polls every 1s tick while pick timer sits at 0. */
let rushPickEndArmedGameId = null;
let winnerModalAutoCloseTimer = null;
/** When visible, modal must close by this time (backup if WebView throttles `setTimeout`). */
let winnerModalAutoCloseDeadlineMs = null;
let lastSpokenCall = null;
let voiceUnlocked = false;
const WINNER_MODAL_AUTO_DISMISS_MS = 3000;
let voiceToastShown = false;

function unlockVoiceOnce() {
  voiceUnlocked = true;
}

// Many Telegram mobile WebViews require a user gesture before speechSynthesis will play.
function armVoiceUnlockGesture() {
  const opts = { once: true, capture: true, passive: true };
  document.addEventListener("pointerdown", unlockVoiceOnce, opts);
  document.addEventListener("touchstart", unlockVoiceOnce, opts);
  document.addEventListener("touchend", unlockVoiceOnce, opts);
  document.addEventListener("click", unlockVoiceOnce, { once: true, capture: true });
}

armVoiceUnlockGesture();

function speakBingoCall(n) {
  try {
    if (!("speechSynthesis" in window) || !("SpeechSynthesisUtterance" in window)) return;

    // Stop anything currently speaking, then speak the newest call.
    window.speechSynthesis.cancel();

    const num = Number(n);
    if (!Number.isFinite(num)) return;
    const letter = bingoLetter(num);
    const utter = new SpeechSynthesisUtterance(`${letter} ${num}`);
    utter.rate = 1.0;
    utter.pitch = 1.0;
    utter.lang = "en-US";
    utter.volume = 1;
    window.speechSynthesis.speak(utter);
  } catch {
    // Silent fallback: if voice is unavailable, just do nothing.
  }
}

function clearWinnerModalAutoCloseTimer() {
  if (winnerModalAutoCloseTimer != null) {
    clearTimeout(winnerModalAutoCloseTimer);
    winnerModalAutoCloseTimer = null;
  }
}

function hideWinnerModalUi() {
  clearWinnerModalAutoCloseTimer();
  winnerModalAutoCloseDeadlineMs = null;
  if (winnerModal) {
    winnerModal.classList.add("hidden");
    winnerModal.setAttribute("aria-hidden", "true");
  }
}

function dismissWinnerModalAndRefresh() {
  hideWinnerModalUi();
  return loadGameAndRender().catch(() => {});
}

function scheduleWinnerModalAutoClose() {
  clearWinnerModalAutoCloseTimer();
  winnerModalAutoCloseDeadlineMs = Date.now() + WINNER_MODAL_AUTO_DISMISS_MS;
  winnerModalAutoCloseTimer = window.setTimeout(() => {
    winnerModalAutoCloseTimer = null;
    dismissWinnerModalAndRefresh();
  }, WINNER_MODAL_AUTO_DISMISS_MS);
}

function showBoardTransitionOverlay() {
  if (!boardTransitionOverlay) return;
  boardTransitionOverlay.classList.remove("hidden");
  boardTransitionOverlay.setAttribute("aria-hidden", "false");
}

function hideBoardTransitionOverlay() {
  if (!boardTransitionOverlay) return;
  boardTransitionOverlay.classList.add("hidden");
  boardTransitionOverlay.setAttribute("aria-hidden", "true");
}

function clearRushLobbyPoll() {
  if (rushLobbyPollTimer != null) {
    clearTimeout(rushLobbyPollTimer);
    rushLobbyPollTimer = null;
  }
}

/** After the 30s pick timer hits 0, poll faster until the server flips the game to running. */
function scheduleRushLobbyPoll() {
  clearRushLobbyPoll();
  const started = Date.now();
  const tick = () => {
    loadGameAndRender()
      .catch(() => {})
      .finally(() => {
        if (!lastGameForUi || lastGameForUi.status !== "lobby") {
          rushLobbyPollTimer = null;
          return;
        }
        if (Date.now() - started > 10000) {
          rushLobbyPollTimer = null;
          return;
        }
        rushLobbyPollTimer = window.setTimeout(tick, 320);
      });
  };
  tick();
}

function enterPlayingBoardView() {
  try {
    window.scrollTo({ top: 0, behavior: "smooth" });
  } catch (_) {
    window.scrollTo(0, 0);
  }
  if (viewPlay) {
    viewPlay.classList.remove("playingBoardEnter");
    void viewPlay.offsetWidth;
    viewPlay.classList.add("playingBoardEnter");
    window.setTimeout(() => {
      if (viewPlay) viewPlay.classList.remove("playingBoardEnter");
    }, 700);
  }
  try {
    window.Telegram?.WebApp?.HapticFeedback?.notificationOccurred?.("success");
  } catch (_) {}
}

function clearLobbyDeadlineAnchor() {
  anchoredLobbyDeadlineMs = NaN;
  anchoredLobbyGameId = null;
}

function anchorLobbyDeadlineFromGame(game) {
  if (!game || game.status !== "lobby") {
    clearLobbyDeadlineAnchor();
    return;
  }
  const iso = game.lobby && game.lobby.pick_deadline_utc;
  let endMs = NaN;
  if (iso) {
    endMs = Date.parse(iso);
    if (!Number.isFinite(endMs) && typeof iso === "string" && !/[zZ]|[+-]\d\d:/.test(iso)) {
      endMs = Date.parse(`${iso}Z`);
    }
  }
  const serverRem =
    game.lobby && game.lobby.pick_seconds_remaining != null
      ? Number(game.lobby.pick_seconds_remaining)
      : NaN;
  if (!Number.isFinite(endMs) && Number.isFinite(serverRem)) {
    endMs = Date.now() + serverRem * 1000;
  }
  // If device clock disagrees with server snapshot, trust pick_seconds_remaining on each poll.
  if (Number.isFinite(endMs) && Number.isFinite(serverRem)) {
    const fromIso = Math.max(0, Math.floor((endMs - Date.now()) / 1000));
    if (Math.abs(fromIso - serverRem) > 2) {
      endMs = Date.now() + serverRem * 1000;
    }
  }
  if (Number.isFinite(endMs)) {
    anchoredLobbyDeadlineMs = endMs;
    anchoredLobbyGameId = game.game_id;
  } else {
    clearLobbyDeadlineAnchor();
  }
}

function setLobbyConnectHint(mode) {
  const wrap = document.getElementById("lobbyConnectHint");
  const text = document.getElementById("lobbyConnectHintText");
  if (!wrap || !text) return;
  if (mode === "hidden") {
    wrap.classList.add("hidden");
    return;
  }
  wrap.classList.remove("hidden");
  if (mode === "token") {
    text.textContent =
      "Starts in stays empty until the server accepts your Telegram login. You have a BOT_TOKEN mismatch: use the API token of the same bot that opens this Mini App in .env as BOT_TOKEN, restart uvicorn, then fully close this chat (swipe away) and open Ethio Bingo again.";
  } else if (mode === "network") {
    text.textContent =
      "Cannot reach the game API. Start the backend and your HTTPS tunnel, then reopen the Mini App. The countdown appears once the lobby loads.";
  } else {
    text.textContent =
      "The lobby did not load. Check the hint at the bottom. When the game loads, Starts in will show live seconds.";
  }
}

/** Seconds left to pick; ticks down every second using anchored deadline (not stale API snapshots). */
function getLobbyPickSecondsRemaining(game) {
  if (!game || game.status !== "lobby") return null;
  if (anchoredLobbyGameId !== game.game_id || !Number.isFinite(anchoredLobbyDeadlineMs)) {
    anchorLobbyDeadlineFromGame(game);
  }
  if (!Number.isFinite(anchoredLobbyDeadlineMs) || anchoredLobbyGameId !== game.game_id) {
    return null;
  }
  return Math.max(0, Math.floor((anchoredLobbyDeadlineMs - Date.now()) / 1000));
}

function updateLobbyCountdownBanner(game) {
  const wrap = document.getElementById("lobbyCountdownBanner");
  if (wrap) wrap.classList.add("hidden");
}

/** Habesha-style yellow bar when not enough players have picked a card. */
function updateLobbyPlayersBanner(game, playersCount, minPlayersToStart) {
  const el = document.getElementById("lobbyPlayersBanner");
  if (!el) return;
  const minP = Number(minPlayersToStart) || 2;
  const pc = Number(playersCount) || 0;
  if (!game || game.status !== "lobby") {
    el.classList.add("hidden");
    return;
  }
  if (pc >= minP) {
    el.classList.add("hidden");
    return;
  }
  el.classList.remove("hidden");
  const need = minP - pc;
  if (pc <= 0) {
    el.textContent = `Waiting for at least ${minP} players to start the game…`;
  } else {
    const joinedWord = pc === 1 ? "player" : "players";
    const moreWord = need === 1 ? "player" : "players";
    el.textContent = `${pc} ${joinedWord} joined. Need at least ${need} more ${moreWord} to start.`;
  }
}

function setHint(msg) {
  const m = msg || "";
  if (statusHintEl) statusHintEl.textContent = m;
  if (playStatusHintEl) playStatusHintEl.textContent = m;
}

let appToastHideTimer = null;

function hideAppToast() {
  if (appToastHideTimer != null) {
    clearTimeout(appToastHideTimer);
    appToastHideTimer = null;
  }
  const el = document.getElementById("appToast");
  if (!el) return;
  el.classList.add("hidden");
  el.setAttribute("aria-hidden", "true");
}

function showAppToast(message) {
  const wrap = document.getElementById("appToast");
  const msgEl = document.getElementById("appToastMsg");
  if (!wrap || !msgEl) return;
  if (appToastHideTimer != null) {
    clearTimeout(appToastHideTimer);
    appToastHideTimer = null;
  }
  msgEl.textContent = message;
  wrap.classList.remove("hidden");
  wrap.setAttribute("aria-hidden", "false");
  appToastHideTimer = window.setTimeout(() => hideAppToast(), 5200);
}

/** FastAPI may return detail as a string or a list of validation errors. */
function formatApiErrorBody(data, status) {
  if (!data || typeof data !== "object") return `HTTP ${status}`;
  const d = data.detail ?? data.error;
  if (d == null) return `HTTP ${status}`;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) {
    return d
      .map((item) => {
        if (item && typeof item === "object" && item.msg) {
          const loc = Array.isArray(item.loc) ? item.loc.filter((x) => x !== "body").join(".") : "";
          return loc ? `${loc}: ${item.msg}` : item.msg;
        }
        return JSON.stringify(item);
      })
      .join("; ");
  }
  return String(d);
}

async function apiFetch(path, options = {}) {
  if (!initData) throw new Error("Missing Telegram initData");

  const headers = options.headers || {};
  headers["X-Telegram-InitData"] = initData;
  if (!headers["Content-Type"] && options.body) {
    headers["Content-Type"] = "application/json";
  }

  let res;
  try {
    res = await fetch(path, { ...options, headers });
  } catch {
    throw new Error("No connection to the API. Start Uvicorn (port 8000) and your tunnel, then reopen Ethio Bingo.");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(formatApiErrorBody(data, res.status));
  }
  return data;
}

function bingoLetter(n) {
  const x = Number(n);
  if (x >= 1 && x <= 15) return "B";
  if (x <= 30) return "I";
  if (x <= 45) return "N";
  if (x <= 60) return "G";
  if (x <= 75) return "O";
  return "?";
}

function updateAvatarStrip() {
  const u = window.Telegram?.WebApp?.initDataUnsafe?.user;
  if (userAvatar && u) {
    const a = (u.first_name || "?").slice(0, 1) + (u.last_name ? u.last_name.slice(0, 1) : "");
    userAvatar.textContent = a.toUpperCase() || "★";
  }
}

function lobbyStakeEtB(game) {
  const n = game != null ? Number(game.min_stake_etb) : NaN;
  return Math.max(1, Math.round(Number.isFinite(n) && n > 0 ? n : 10));
}

function updateLobbyStrip(game, yourPickNumber, hasBet) {
  if (!topWallet || !topStake || !topCard || !topStarting) return;
  const startingLab = document.getElementById("topStartingLab");
  topWallet.textContent = String(Math.floor(lastBalanceEtb));
  const stake = game ? lobbyStakeEtB(game) : 10;
  topStake.textContent = String(stake);
  const tick = yourPickNumber != null ? yourPickNumber : selectedPick;
  topCard.textContent = tick != null ? String(tick) : "--";
  if (game && game.status === "lobby") {
    const sec = getLobbyPickSecondsRemaining(game);
    topStarting.textContent = sec != null ? `${sec}s` : "—";
    if (startingLab) startingLab.textContent = "START";
  } else if (game && game.status === "running") {
    const cc = game.current_call;
    if (cc != null && cc !== undefined) {
      topStarting.textContent = `${bingoLetter(cc)}-${cc}`;
    } else {
      topStarting.textContent = "—";
    }
    if (startingLab) startingLab.textContent = "Call";
  } else {
    topStarting.textContent = "—";
    if (startingLab) startingLab.textContent = "Round";
  }
}

function _lobbyStakeNeededEtB(game) {
  return lobbyStakeEtB(game);
}

function updateLobbyStakeUi(game, hasBet, yourPickNumber) {
  const stakeSummary = document.getElementById("lobbyStakeSummary");
  const nonHostHint = document.getElementById("lobbyNonHostHint");
  if (!stakeSummary || !nonHostHint) return;
  if (!game || game.status !== "lobby") return;

  nonHostHint.classList.remove("hidden");
  nonHostHint.textContent = "The round starts only when the pick timer reaches zero — others can still join until then.";

  if (hasBet && game.your_bet) {
    stakeSummary.classList.remove("hidden");
    const st = Math.round(Number(game.your_bet.stake_etb) || 0);
    stakeSummary.textContent = `Stake ${st} ETB · Card #${yourPickNumber != null ? yourPickNumber : "—"} — wait for the timer to finish.`;
  } else {
    stakeSummary.classList.add("hidden");
  }
}

function resetLobbyStakeUiForPlay() {
  const stakeSummary = document.getElementById("lobbyStakeSummary");
  const nonHostHint = document.getElementById("lobbyNonHostHint");
  if (stakeSummary) stakeSummary.classList.add("hidden");
  if (nonHostHint) nonHostHint.classList.add("hidden");
}

function updateBalanceBanner(game, hasBet) {
  if (!balanceBanner || !balanceBannerText || !game) return;
  if (game.status !== "lobby" || hasBet) {
    balanceBanner.classList.add("hidden");
    return;
  }
  const need = _lobbyStakeNeededEtB(game);
  const poor = lastBalanceEtb < need;
  balanceBanner.classList.toggle("hidden", !poor);
  if (poor) {
    balanceBannerText.textContent = `You need ${need} ETB to play. Your balance: ${Math.floor(lastBalanceEtb)} ETB.`;
  }
}

function refreshLobbyBettingControls() {
  const game = lastGameForUi;
  if (!game || game.status !== "lobby") return;
  const hasBet = !!(game.your_bet && game.your_bet.picked_numbers && game.your_bet.picked_numbers.length);
  updateBalanceBanner(game, hasBet);
}

function populateStats(game) {
  if (!statsRow) return;
  const gross = Number(game.prize_pool_etb) || 0;
  const net =
    game.net_prize_pool_etb != null ? Number(game.net_prize_pool_etb) : gross;
  // Lobby: show total stakes; once the round is live, Derash is the winner-facing pool after house cut.
  const derash =
    game.status === "lobby" ? gross : net;
  const pool = Math.round(derash);
  const players = game.players_count != null ? game.players_count : 0;
  const bet = game.min_stake_etb != null ? game.min_stake_etb : 10;
  const calls = game.call_count != null ? game.call_count : (game.called_numbers || []).length;
  statsRow.innerHTML = `
    <div class="statBox"><span class="statLab">Derash</span><span class="statVal">${pool}</span></div>
    <div class="statBox"><span class="statLab">Players</span><span class="statVal">${players}</span></div>
    <div class="statBox"><span class="statLab">Bet</span><span class="statVal">${bet}</span></div>
    <div class="statBox"><span class="statLab">Call</span><span class="statVal">${calls}</span></div>
  `;
}

function populateLiveCall(game) {
  if (!currentCallBig || !recentCallsEl) return;
  const cur = game.current_call;
  if (cur != null && cur !== undefined) {
    currentCallBig.textContent = `${bingoLetter(cur)}-${cur}`;
  } else {
    currentCallBig.textContent = "—";
  }
  recentCallsEl.innerHTML = "";
  // Habesha style: show only 3 recent calls to save space.
  const recent = (game.recent_calls || []).slice(0, 3);
  recent.forEach((n) => {
    const chip = document.createElement("span");
    const L = bingoLetter(n);
    const colIdx = { B: 0, I: 1, N: 2, G: 3, O: 4 }[L] ?? 0;
    chip.className = `recentChip recentChip--c${colIdx}`;
    chip.textContent = `${L}-${n}`;
    recentCallsEl.appendChild(chip);
  });

  // Voice: speak each new call only once, and only during `running`.
  if (game.status === "running" && cur != null && cur !== undefined) {
    const cn = Number(cur);
    if (Number.isFinite(cn) && cn !== lastSpokenCall) {
      lastSpokenCall = cn;
      speakBingoCall(cn);
    }
  }
}

function renderMasterBingo(game) {
  if (!masterBingo) return;
  masterBingo.innerHTML = "";
  const called = new Set((game.called_numbers || []).map((x) => Number(x)));
  const current = game.current_call != null ? Number(game.current_call) : null;
  const letters = ["B", "I", "N", "G", "O"];
  const ranges = [
    [1, 15],
    [16, 30],
    [31, 45],
    [46, 60],
    [61, 75],
  ];
  const wrap = document.createElement("div");
  wrap.className = "bingoCols";
  for (let c = 0; c < 5; c++) {
    const col = document.createElement("div");
    col.className = "bingoCol";
    const head = document.createElement("div");
    head.className = `bingoColHead h${c}`;
    head.textContent = letters[c];
    col.appendChild(head);
    const [lo, hi] = ranges[c];
    for (let n = lo; n <= hi; n++) {
      const cell = document.createElement("div");
      cell.className = "mCell";
      cell.textContent = String(n);
      if (called.has(n)) cell.classList.add("called");
      if (current != null && n === current) cell.classList.add("currentCall");
      col.appendChild(cell);
    }
    wrap.appendChild(col);
  }
  masterBingo.appendChild(wrap);
}

/** 10-column ticket grid preview before API responds (or when auth fails). */
function renderTicketGridStatic() {
  if (!boardEl) return;
  boardEl.innerHTML = "";
  boardEl.classList.add("boardDense");
  for (let n = 1; n <= 400; n++) {
    const cell = document.createElement("div");
    cell.className = "num numStaticPreview";
    cell.textContent = String(n);
    boardEl.appendChild(cell);
  }
}

async function trySecureTicketPick(n) {
  const game = lastGameForUi;
  if (securingPick || !game || game.status !== "lobby") return;
  if (game.your_role === "spectator") return;
  const hasBet = !!(game.your_bet && game.your_bet.picked_numbers && game.your_bet.picked_numbers.length);
  if (hasBet) return;
  const pickOpen = !game.lobby || game.lobby.pick_open !== false;
  if (!pickOpen) {
    setHint("Card selection time is over. The round is starting.");
    return;
  }
  if (n < game.board.min || n > game.board.max) return;

  const need = _lobbyStakeNeededEtB(game);
  if (lastBalanceEtb < need) {
    updateBalanceBanner(game, false);
    setHint(`You need ${need} ETB to secure a number. Your balance: ${Math.floor(lastBalanceEtb)} ETB.`);
    return;
  }

  securingPick = true;
  try {
    const stake = lobbyStakeEtB(game);

    await apiFetch(`/games/${game.game_id}/bets`, {
      method: "POST",
      body: JSON.stringify({ stake_etb: stake, pick_number: n }),
    });
    selectedPick = n;
    pickBoxEl.textContent = String(n);
    setHint("Card secured. The round starts when the pick timer reaches zero.");
    await loadBalance().catch(() => {});
    await loadGameAndRender().catch((e) => setHint(e.message));
  } catch (e) {
    setHint(e.message);
  } finally {
    securingPick = false;
  }
}

async function tryChangeLobbyPick(n) {
  const game = lastGameForUi;
  if (securingPick || !game || game.status !== "lobby") return;
  if (game.your_role === "spectator") return;
  const hasBet = !!(game.your_bet && game.your_bet.picked_numbers && game.your_bet.picked_numbers.length);
  if (!hasBet) return;
  const pickOpen = !game.lobby || game.lobby.pick_open !== false;
  if (!pickOpen) {
    setHint("Card selection time is over. The round is starting.");
    return;
  }
  if (n < game.board.min || n > game.board.max) return;

  const need = _lobbyStakeNeededEtB(game);
  const held = Number(game.your_bet.stake_etb) || 0;
  const spendable = lastBalanceEtb + held;
  if (spendable < need) {
    updateBalanceBanner(game, false);
    setHint(`You need ${need} ETB for this stake (including your current pick). Available: ${Math.floor(spendable)} ETB.`);
    return;
  }

  securingPick = true;
  try {
    const stake = lobbyStakeEtB(game);

    await apiFetch(`/games/${game.game_id}/bets`, {
      method: "POST",
      body: JSON.stringify({ stake_etb: stake, pick_number: n }),
    });
    selectedPick = n;
    pickBoxEl.textContent = String(n);
    showAppToast(`Card changed — now ${n}`);
    await loadBalance().catch(() => {});
    await loadGameAndRender().catch((e) => setHint(e.message));
  } catch (e) {
    setHint(e.message);
  } finally {
    securingPick = false;
  }
}

async function tryReleaseLobbyPick() {
  const game = lastGameForUi;
  if (securingPick || !game || game.status !== "lobby") return;
  if (game.your_role === "spectator") return;
  const hasBet = !!(game.your_bet && game.your_bet.picked_numbers && game.your_bet.picked_numbers.length);
  if (!hasBet) return;
  const pickOpen = !game.lobby || game.lobby.pick_open !== false;
  if (!pickOpen) {
    setHint("Card selection time is over — you cannot release now.");
    return;
  }

  const releasedNum =
    game.your_bet && game.your_bet.picked_numbers && game.your_bet.picked_numbers.length
      ? Number(game.your_bet.picked_numbers[0])
      : null;

  securingPick = true;
  try {
    await apiFetch(`/games/${game.game_id}/lobby/release-pick`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    selectedPick = null;
    pickBoxEl.textContent = "None";
    showAppToast(releasedNum != null ? `Card ${releasedNum} released` : "Card released");
    await loadBalance().catch(() => {});
    await loadGameAndRender().catch((e) => setHint(e.message));
  } catch (e) {
    setHint(e.message);
  } finally {
    securingPick = false;
  }
}

/** Lobby: 10-column grid; tap = secure ticket (places bet) when balance ≥ stake. */
function renderLobbyBoard(game, takenByOthers, yourPickNumber, hasBet, selectedPickLocal) {
  if (!boardEl || !game) return;
  const min = game.board.min;
  const max = game.board.max;
  boardEl.innerHTML = "";
  const takenSet = new Set((takenByOthers || []).map((x) => Number(x)));
  const pickForHighlight = selectedPickLocal != null ? selectedPickLocal : yourPickNumber;
  const canPlace = game.status === "lobby" && game.your_role !== "spectator";
  const pickOpen = !game.lobby || game.lobby.pick_open !== false;
  boardEl.classList.toggle("boardDense", max - min > 100);

  for (let n = min; n <= max; n++) {
    const cell = document.createElement("div");
    cell.className = "num";
    cell.textContent = String(n);

    if (pickForHighlight != null && n === pickForHighlight) {
      cell.classList.add("yourPick");
    } else if (takenSet.has(n)) {
      cell.classList.add("taken");
    }

    if (!canPlace || !pickOpen) {
      cell.classList.add("disabled");
    } else if (!hasBet) {
      if (takenSet.has(n)) {
        cell.classList.add("disabled");
      } else {
        cell.addEventListener("click", () => {
          trySecureTicketPick(n);
        });
      }
    } else if (pickForHighlight != null && n === pickForHighlight) {
      cell.addEventListener("click", () => {
        tryReleaseLobbyPick();
      });
    } else if (!takenSet.has(n)) {
      cell.addEventListener("click", () => {
        tryChangeLobbyPick(n);
      });
    } else {
      cell.classList.add("disabled");
    }

    boardEl.appendChild(cell);
  }
}

function renderStatsPlaceholder(betDefault) {
  if (!statsRow) return;
  const b = betDefault != null && betDefault > 0 ? betDefault : 10;
  statsRow.innerHTML = `
    <div class="statBox"><span class="statLab">Derash</span><span class="statVal">0</span></div>
    <div class="statBox"><span class="statLab">Players</span><span class="statVal">0</span></div>
    <div class="statBox"><span class="statLab">Bet</span><span class="statVal">${b}</span></div>
    <div class="statBox"><span class="statLab">Call</span><span class="statVal">0</span></div>
  `;
}

function isMarked(marked, r, c) {
  return (marked || []).some((m) => Number(m[0]) === r && Number(m[1]) === c);
}

function renderLobbyCardPreview(game) {
  if (!lobbyCardPreview || !lobbyPreviewLetters || !lobbyPreviewGrid) return;
  const inLobby = game && game.status === "lobby";
  const card = game && game.your_bet && game.your_bet.your_card;
  const ok = card && Array.isArray(card) && card.length === 5;
  if (!inLobby || !ok) {
    lobbyCardPreview.classList.add("hidden");
    return;
  }
  lobbyCardPreview.classList.remove("hidden");
  const letters = ["B", "I", "N", "G", "O"];
  lobbyPreviewLetters.innerHTML = letters
    .map((L) => `<span class="lobbyPreviewLetterTile">${L}</span>`)
    .join("");
  lobbyPreviewGrid.innerHTML = "";
  for (let r = 0; r < 5; r++) {
    for (let c = 0; c < 5; c++) {
      const val = Number(card[r][c]);
      const cell = document.createElement("div");
      cell.className = "lobbyPreviewCell";
      if (val === 0) {
        cell.textContent = "★";
        cell.classList.add("lobbyPreviewFree");
      } else {
        cell.textContent = String(val);
      }
      lobbyPreviewGrid.appendChild(cell);
    }
  }
}

function renderMyCard(game) {
  if (game && game.status === "lobby") {
    myCardSection.classList.add("hidden");
    return;
  }
  const forceSpectatorBecauseBlocked =
    !!(game && game.status === "running" && game.your_bet && game.your_bet.bingo_claim_blocked);
  if (game && (game.your_role === "spectator" || forceSpectatorBecauseBlocked)) {
    myCardSection.classList.add("hidden");
    return;
  }
  const card = game.your_bet && game.your_bet.your_card;
  const marked = (game.your_bet && game.your_bet.marked) || [];
  if (!card || !Array.isArray(card) || card.length !== 5) {
    myCardSection.classList.add("hidden");
    return;
  }

  myCardSection.classList.remove("hidden");
  bingoHeadEl.innerHTML = ["B", "I", "N", "G", "O"]
    .map((l, i) => `<span class="bingoHeadTile h${i}">${l}</span>`)
    .join("");

  const running = game.status === "running";
  const isPlayer = game.your_role === "player";
  const settled = game.your_bet && game.your_bet.settled;
  const bingoBlocked = !!(game.your_bet && game.your_bet.bingo_claim_blocked);

  myCardEl.innerHTML = "";

  for (let r = 0; r < 5; r++) {
    for (let c = 0; c < 5; c++) {
      const val = Number(card[r][c]);
      const cell = document.createElement("div");
      cell.className = "cell";
      const mk = isMarked(marked, r, c);
      if (val === 0) {
        cell.textContent = "★";
        cell.classList.add("free");
      } else {
        cell.textContent = String(val);
      }
      if (mk) {
        cell.classList.add("marked");
      }

      // After a false BINGO, the user should stop playing this round:
      // disable any further marking/unmarking, not only the BINGO button.
      const canTapMark = running && isPlayer && !settled && !bingoBlocked && !mk;
      const canTapUnmark = running && isPlayer && !settled && !bingoBlocked && mk && val !== 0;
      if (canTapMark) {
        cell.classList.add("canTap");
        cell.addEventListener("click", async () => {
          try {
            await apiFetch(`/games/${lastGameId}/mark`, {
              method: "POST",
              body: JSON.stringify({ row: r, col: c }),
            });
            await loadGameAndRender().catch(() => {});
          } catch (e) {
            setHint(e.message);
          }
        });
      } else if (canTapUnmark) {
        cell.classList.add("canTap");
        cell.addEventListener("click", async () => {
          try {
            await apiFetch(`/games/${lastGameId}/unmark`, {
              method: "POST",
              body: JSON.stringify({ row: r, col: c }),
            });
            await loadGameAndRender().catch(() => {});
          } catch (e) {
            setHint(e.message);
          }
        });
      }

      myCardEl.appendChild(cell);
    }
  }

  claimBingoBtn.disabled = !(running && isPlayer && !settled && !bingoBlocked);
  claimBingoBtn.title = bingoBlocked ? "False BINGO — blocked for this round" : "";
}

function renderWinnerCardVisual(card, calledNumbers, highlightNum, winningLineCells) {
  if (!card || card.length !== 5) {
    return "<p>No card</p>";
  }
  const called = new Set((calledNumbers || []).map(Number));
  const hi = highlightNum != null ? Number(highlightNum) : null;
  const winLineSet = new Set(
    (winningLineCells || []).map((x) => `${Number(x[0])},${Number(x[1])}`)
  );
  const letters = ["B", "I", "N", "G", "O"];
  let html = '<div class="wcSheet">';
  html += '<div class="wcLettersRow">';
  letters.forEach((L, i) => {
    html += `<span class="wcL h${i}">${L}</span>`;
  });
  html += '</div><div class="wcGrid">';
  for (let r = 0; r < 5; r++) {
    for (let c = 0; c < 5; c++) {
      const v = Number(card[r][c]);
      const inWinLine = winLineSet.has(`${r},${c}`);
      let cls = "wcCell";
      if (v === 0) {
        cls += " wcFree";
        if (inWinLine) cls += " wcWinLine";
        html += `<div class="${cls}">★</div>`;
      } else {
        if (inWinLine) cls += " wcWinLine";
        else if (called.has(v)) cls += " wcCalled";
        if (hi != null && v === hi) cls += " wcWinNum";
        const inner =
          hi != null && v === hi
            ? `<span class="wcWinNumInner"><span class="wcWinNumDigit">${v}</span><span class="wcBingoCallStar" aria-hidden="true">★</span></span>`
            : String(v);
        html += `<div class="${cls}">${inner}</div>`;
      }
    }
  }
  html += "</div>";
  if (hi != null) {
    html += `<div class="wcWinBar">BINGO on <span class="wcWinBarStar" aria-hidden="true">★</span> ${bingoLetter(hi)}-${hi}</div>`;
  }
  html += "</div>";
  return html;
}

function winnerModalFooterText(payoutEtB, grossPoolEtB, houseRakeEtB) {
  const p = Number(payoutEtB || 0);
  const g = Number(grossPoolEtB || 0);
  const r = Number(houseRakeEtB || 0);
  if (g > 0 && r > 0) {
    return `Pool ${g.toFixed(0)} ETB · House ${r.toFixed(0)} ETB · You receive ${p.toFixed(0)} ETB`;
  }
  return "ETHIO BINGO";
}

/** Bingo claim ends the round and immediately opens a new lobby; show win UI from POST body. */
function showBingoWinnerModalFromClaim(data) {
  const wm = data && data.winner_modal;
  if (!wm || !winnerModal) return;
  if (data.finished_game_id) {
    lastPreviousRoundWinnerShownId = data.finished_game_id;
  }
  lastWinnerModalGameId = data.finished_game_id || lastWinnerModalGameId;
  const me = window.Telegram?.WebApp?.initDataUnsafe?.user;
  const name = me?.username ? `@${me.username}` : me?.first_name || "You";
  if (winnerHeadTitle) {
    winnerHeadTitle.textContent = "You win!";
    winnerHeadTitle.classList.add("winnerGreen");
  }
  winnerPrizeAmt.textContent = `${Number(wm.payout_etb || 0).toFixed(0)} ETB`;
  const pattern = wm.winning_pattern ? String(wm.winning_pattern) : "";
  const boardNum = wm.board_number != null && wm.board_number !== undefined ? Number(wm.board_number) : null;
  const lineCells = wm.winning_line_cells || [];
  const metaRow =
    pattern || boardNum != null
      ? `<div class="wcMetaRow">${pattern ? `<span class="wcPatternPopover">${pattern}</span>` : ""}${
          boardNum != null ? `<span class="wcBoardPill">Board #${boardNum}</span>` : ""
        }</div>`
      : "";
  const card = wm.card || [];
  const lastC = wm.last_call != null ? wm.last_call : null;
  winnerCardWrap.innerHTML = `
      <div class="wcPlayer">${name}</div>
      ${metaRow}
      ${renderWinnerCardVisual(card, wm.called_numbers || [], lastC, lineCells)}
    `;
  if (winnerFooterBot) {
    winnerFooterBot.textContent = winnerModalFooterText(wm.payout_etb, wm.gross_pool_etb, wm.house_rake_etb);
  }
  winnerModal.classList.remove("hidden");
  winnerModal.setAttribute("aria-hidden", "false");
  scheduleWinnerModalAutoClose();
}

/** After a round ends, the API returns a new lobby with `previous_round` so every player sees the winning card once. */
function maybeShowPreviousRoundWinnerModal(game) {
  const pr = game.previous_round;
  if (!pr || !pr.source_game_id || !pr.winner) return;
  if (lastPreviousRoundWinnerShownId === pr.source_game_id) return;

  lastPreviousRoundWinnerShownId = pr.source_game_id;

  const w = pr.winner;
  const me = window.Telegram?.WebApp?.initDataUnsafe?.user;
  const name = w.username ? `@${w.username}` : w.telegram_user_id != null ? `Player ${w.telegram_user_id}` : "Winner";
  const you = me && Number(me.id) === Number(w.telegram_user_id);

  if (winnerHeadTitle) {
    winnerHeadTitle.textContent = you ? "You win!" : `${name} wins!`;
    winnerHeadTitle.classList.add("winnerGreen");
  }
  winnerPrizeAmt.textContent = `${Number(w.payout_etb || 0).toFixed(0)} ETB`;
  const card = w.card || [];
  const lastC = w.last_call != null ? w.last_call : null;
  const pattern = w.winning_pattern ? String(w.winning_pattern) : "";
  const boardNum = w.board_number != null && w.board_number !== undefined ? Number(w.board_number) : null;
  const lineCells = w.winning_line_cells || [];
  const calledNums = pr.called_numbers || [];
  const metaRow =
    pattern || boardNum != null
      ? `<div class="wcMetaRow">${pattern ? `<span class="wcPatternPopover">${pattern}</span>` : ""}${
          boardNum != null ? `<span class="wcBoardPill">Board #${boardNum}</span>` : ""
        }</div>`
      : "";
  winnerCardWrap.innerHTML = `
      <div class="wcPlayer">${name}</div>
      ${metaRow}
      ${renderWinnerCardVisual(card, calledNums, lastC, lineCells)}
    `;
  if (winnerFooterBot) {
    winnerFooterBot.textContent = winnerModalFooterText(w.payout_etb, w.gross_pool_etb, w.house_rake_etb);
  }
  winnerModal.classList.remove("hidden");
  winnerModal.setAttribute("aria-hidden", "false");
  scheduleWinnerModalAutoClose();
}

function maybeShowWinnerModal(game) {
  if (game.status !== "finished" || !lastGameId) return;
  if (lastWinnerModalGameId === game.game_id) return;

  const w = game.winner;
  const me = window.Telegram?.WebApp?.initDataUnsafe?.user;

  lastWinnerModalGameId = game.game_id;

  if (w && w.telegram_user_id != null) {
    const name = w.username || `Player ${w.telegram_user_id}`;
    const you = me && Number(me.id) === Number(w.telegram_user_id);
    if (winnerHeadTitle) {
      winnerHeadTitle.textContent = you ? "You win!" : `${name} wins!`;
      winnerHeadTitle.classList.add("winnerGreen");
    }
    winnerPrizeAmt.textContent = `${Number(w.payout_etb || 0).toFixed(0)} ETB`;
    const card = w.card || [];
    const lastC = w.last_call != null ? w.last_call : null;
    const pattern = w.winning_pattern ? String(w.winning_pattern) : "";
    const boardNum = w.board_number != null && w.board_number !== undefined ? Number(w.board_number) : null;
    const lineCells = w.winning_line_cells || [];
    const metaRow =
      pattern || boardNum != null
        ? `<div class="wcMetaRow">${pattern ? `<span class="wcPatternPopover">${pattern}</span>` : ""}${
            boardNum != null ? `<span class="wcBoardPill">Board #${boardNum}</span>` : ""
          }</div>`
        : "";
    winnerCardWrap.innerHTML = `
      <div class="wcPlayer">${name}</div>
      ${metaRow}
      ${renderWinnerCardVisual(card, game.called_numbers, lastC, lineCells)}
    `;
  } else {
    if (winnerHeadTitle) {
      winnerHeadTitle.textContent = "Round over";
      winnerHeadTitle.classList.remove("winnerGreen");
    }
    winnerPrizeAmt.textContent = "—";
    winnerCardWrap.innerHTML = "<p>No bingo claimed before the last ball.</p>";
  }

  if (winnerFooterBot) {
    if (w && w.gross_pool_etb != null && w.house_rake_etb != null) {
      winnerFooterBot.textContent = winnerModalFooterText(w.payout_etb, w.gross_pool_etb, w.house_rake_etb);
    } else {
      winnerFooterBot.textContent = "ETHIO BINGO";
    }
  }

  winnerModal.classList.remove("hidden");
  winnerModal.setAttribute("aria-hidden", "false");
  scheduleWinnerModalAutoClose();
}

async function loadBalance() {
  const data = await apiFetch("/wallet/balance");
  lastBalanceEtb = Number(data.balance_etb);
  balanceEl.textContent = `${lastBalanceEtb.toFixed(0)} ETB`;
  subtitle.textContent = `@${data.username || "user"}`;
  if (topWallet) topWallet.textContent = String(Math.floor(lastBalanceEtb));
  refreshLobbyBettingControls();
}

async function loadGameAndRender() {
  if (
    winnerModal &&
    !winnerModal.classList.contains("hidden") &&
    winnerModalAutoCloseDeadlineMs != null &&
    Date.now() >= winnerModalAutoCloseDeadlineMs
  ) {
    hideWinnerModalUi();
  }

  const prevId = lastGameId;
  const game = await apiFetch("/games/active");
  if (prevId != null && prevId !== game.game_id && game.status === "lobby") {
    selectedPick = null;
    rushPickEndArmedGameId = null;
    clearLobbyDeadlineAnchor();
    // Keep winner modal open if visible (e.g. after BINGO); user closes with OK.
  }
  lastGameId = game.game_id;
  lastGameForUi = game;
  setLobbyConnectHint("hidden");

  if (game.status !== "lobby") {
    hideBoardTransitionOverlay();
    clearRushLobbyPoll();
    rushPickEndArmedGameId = null;
  }

  if (game.status === "lobby") {
    anchorLobbyDeadlineFromGame(game);
  } else {
    clearLobbyDeadlineAnchor();
  }

  updateAvatarStrip();

  const yourPickNumber =
    game.your_bet && game.your_bet.picked_numbers && game.your_bet.picked_numbers.length
      ? Number(game.your_bet.picked_numbers[0])
      : null;

  const hasBet = !!(game.your_bet && game.your_bet.picked_numbers && game.your_bet.picked_numbers.length);

  if (selectedPick == null && yourPickNumber) {
    selectedPick = yourPickNumber;
  }

  const minPlayersToStart = game.min_players_to_start != null ? Number(game.min_players_to_start) : 2;
  const playersCount = game.players_count != null ? Number(game.players_count) : 0;

  updateLobbyStrip(game, yourPickNumber, hasBet);
  updateLobbyCountdownBanner(game);
  updateLobbyPlayersBanner(game, playersCount, minPlayersToStart);
  updateBalanceBanner(game, hasBet);

  if (!hasBet) {
    pickBoxEl.textContent = selectedPick != null ? String(selectedPick) : "None";
  } else {
    pickBoxEl.textContent = yourPickNumber != null ? String(yourPickNumber) : "None";
  }

  const inLobby = game.status === "lobby";

  if (viewSelect && viewPlay) {
    viewSelect.classList.toggle("hidden", !inLobby);
    viewPlay.classList.toggle("hidden", inLobby);
  }
  // Habesha layout: hide the lobby pill strip when the round is live.
  if (lobbyStrip) {
    lobbyStrip.classList.toggle("hidden", !inLobby);
  }
  // Habesha layout: omit username + balance on the live play page.
  if (appHeader && headerActions && subtitle) {
    const playMode = !inLobby;
    subtitle.classList.toggle("hidden", playMode);
    headerActions.classList.toggle("hidden", playMode);
  }

  if (!inLobby) {
    populateStats(game);
  }

  if (inLobby) {
    if (pickBoardLabel) {
      pickBoardLabel.textContent =
        game.board && game.board.min != null && game.board.max != null
          ? `Pick your card number (${game.board.min}–${game.board.max})`
          : "Pick your card number";
    }
    if (lobbyTicketSection) lobbyTicketSection.classList.remove("hidden");
    if (playBingoSection) playBingoSection.classList.add("hidden");
    if (ticketLegendLobby) ticketLegendLobby.classList.remove("hidden");
    if (pickBoardLabel) pickBoardLabel.classList.remove("hidden");
    if (lobbyControls) lobbyControls.classList.remove("hidden");
    const taken = game.taken_ticket_numbers || [];
    renderLobbyBoard(game, taken, yourPickNumber, hasBet, selectedPick);
    updateLobbyStakeUi(game, hasBet, yourPickNumber);
  } else {
    resetLobbyStakeUiForPlay();
    if (lobbyTicketSection) lobbyTicketSection.classList.add("hidden");
    if (playBingoSection) playBingoSection.classList.remove("hidden");
    if (ticketLegendLobby) ticketLegendLobby.classList.add("hidden");
    if (pickBoardLabel) pickBoardLabel.classList.add("hidden");
    if (lobbyControls) lobbyControls.classList.add("hidden");
    if (liveCallCard) {
      liveCallCard.classList.remove("hidden");
      liveCallCard.classList.remove("liveCallCard--lobby");
    }
    if (sideStarted) {
      sideStarted.textContent = game.status === "running" ? "Started" : "Finished";
      sideStarted.classList.toggle("sideLive", game.status === "running");
    }
    renderMasterBingo(game);
    populateLiveCall(game);
  }

  const forceSpectatorBecauseBlocked =
    !!(game.status === "running" && game.your_bet && game.your_bet.bingo_claim_blocked);
  const isSpectator = game.your_role === "spectator" || forceSpectatorBecauseBlocked;
  const showSpectatorHero =
    isSpectator && (game.status === "running" || game.status === "finished");
  if (spectatorHero) {
    spectatorHero.classList.toggle("hidden", !showSpectatorHero);
  }
  if (showSpectatorHero && spectatorHeroLead && spectatorHeroHint) {
    const bingoBlocked =
      game.your_bet && game.your_bet.bingo_claim_blocked ? true : false;
    if (bingoBlocked && game.status === "running") {
      spectatorHeroLead.textContent = "Disqualified - False BINGO";
      spectatorHeroHint.textContent =
        "You are now watching this game as a spectator. Wait for this game to finish, then you can join the next round from the lobby!";
      if (spectatorEyeIcon) spectatorEyeIcon.classList.add("hidden");
      if (disqualifiedFalseBingoXIcon) disqualifiedFalseBingoXIcon.classList.remove("hidden");
    } else {
      if (spectatorEyeIcon) spectatorEyeIcon.classList.remove("hidden");
      if (disqualifiedFalseBingoXIcon) disqualifiedFalseBingoXIcon.classList.add("hidden");
      if (game.status === "finished") {
        spectatorHeroLead.textContent = "This round has ended";
        spectatorHeroHint.textContent =
          "You watched without a card. Open ETHIO BINGO again from the bot for the next lobby — pick a number before the timer runs out!";
      } else {
        spectatorHeroLead.textContent = "You are watching this game";
        spectatorHeroHint.textContent =
          "You did not secure a card before the round started, so you are in spectator mode. Wait for this game to finish, then join the next round from the lobby!";
      }
    }
  }

  renderLobbyCardPreview(game);
  renderMyCard(game);

  maybeShowPreviousRoundWinnerModal(game);
  maybeShowWinnerModal(game);

  let runningPlayerHint = "Tap squares on your card to mark them, then BINGO! (Only called numbers count toward a valid win.)";
  if (
    game.status === "running" &&
    game.your_role === "player" &&
    game.your_bet &&
    game.your_bet.picked_numbers &&
    game.your_bet.picked_numbers.length
  ) {
    const tno = Number(game.your_bet.picked_numbers[0]);
    runningPlayerHint = `Card #${tno} · Tap to mark; only a full valid line with called numbers wins. False BINGO locks you out this round.`;
  }
  if (
    game.status === "running" &&
    game.your_role === "player" &&
    game.your_bet &&
    game.your_bet.bingo_claim_blocked
  ) {
    runningPlayerHint =
      "False BINGO — you cannot press BINGO again this round. Wait for the round to finish.";
  }

  let finishedHint = "Round finished.";
  if (game.status === "finished" && game.your_bet && game.your_bet.picked_numbers && game.your_bet.picked_numbers.length) {
    if (game.your_bet.win) {
      finishedHint = `You won! +${Number(game.your_bet.payout_etb || 0).toFixed(0)} ETB`;
    } else if (game.your_bet.settled) {
      finishedHint = "Round finished — no win this time.";
    }
  }

  const lobbyHasCard = !!(
    game.status === "lobby" &&
    game.your_bet &&
    game.your_bet.picked_numbers &&
    game.your_bet.picked_numbers.length
  );
  if (game.status === "lobby") {
    setHint(
      lobbyHasCard
        ? "Card locked — the round starts only when the pick timer hits zero. Tap your number to refund, or another free number to switch."
        : "Tap a free card on the board to join at this round’s stake."
    );
  } else {
    setHint(
      game.status === "running"
        ? isSpectator
          ? "Spectator mode — you have no card this round. Watch the board; next lobby, pick a number before the timer hits 0."
          : runningPlayerHint
        : finishedHint
    );
  }

  if (prevViewStatusForTransition === "lobby" && game.status === "running") {
    enterPlayingBoardView();
  }
  prevViewStatusForTransition = game.status;
}

function waitForInitData(maxMs = 12000) {
  return new Promise((resolve, reject) => {
    const t0 = Date.now();
    const tick = () => {
      const d = window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.initData;
      if (d && String(d).trim()) {
        resolve(String(d));
        return;
      }
      if (Date.now() - t0 > maxMs) {
        reject(new Error("Telegram did not send login data. Close the Mini App and open Ethio Bingo again."));
        return;
      }
      setTimeout(tick, 80);
    };
    tick();
  });
}

async function main() {
  if (!window.Telegram || !window.Telegram.WebApp) {
    subtitle.textContent = "Not in Telegram";
    setHint("Open this link only via your bot: tap Play Ethio Bingo inside the Telegram app (not Chrome/Safari).");
    return;
  }

  startParam = new URLSearchParams(window.location.search).get("start");
  window.Telegram.WebApp.ready();
  applyMiniAppDisplayTitle();
  try {
    window.Telegram.WebApp.expand();
  } catch (_) {}
  applyMiniAppDisplayTitle();

  renderStatsPlaceholder(10);
  if (viewSelect) viewSelect.classList.remove("hidden");
  if (viewPlay) viewPlay.classList.add("hidden");
  if (lobbyTicketSection) lobbyTicketSection.classList.remove("hidden");
  renderTicketGridStatic();
  if (ticketLegendLobby) ticketLegendLobby.classList.remove("hidden");
  if (pickBoardLabel) pickBoardLabel.classList.remove("hidden");
  if (lobbyControls) lobbyControls.classList.add("hidden");

  try {
    initData = await waitForInitData();
  } catch (e) {
    subtitle.textContent = "Login needed";
    setHint(e.message);
    return;
  }

  let authOk = false;
  await loadBalance()
    .then(() => {
      authOk = true;
    })
    .catch((e) => {
      const detail = e && e.message ? e.message : String(e);
      const tokenMismatch =
        /invalid initdata hash/i.test(detail) ||
        /missing hash/i.test(detail) ||
        /missing initdata or bot token/i.test(detail) ||
        /initdata expired/i.test(detail);
      const noNet = /no connection to the api/i.test(detail);
      subtitle.textContent = tokenMismatch
        ? "BOT_TOKEN ≠ this bot"
        : noNet
          ? "Can't reach API"
          : "Can't load account";
      setLobbyConnectHint(tokenMismatch ? "token" : noNet ? "network" : "generic");
      setHint(
        `${detail}\n\n` +
          (tokenMismatch
            ? "The Mini App is opened from one Telegram bot, but uvicorn is using a different bot's token.\n\nFix: BotFather → select the SAME bot you use for Ethio Bingo → API Token → put it in .env as BOT_TOKEN → save → restart uvicorn → fully close the Mini App (swipe away) and open Ethio Bingo again.\n\n"
            : "") +
          `If you still see hash errors: confirm only one uvicorn is running, no old terminal, and .env is next to the project folder the app loads settings from.`
      );
    });

  // Many Telegram mobile WebViews block speechSynthesis until user gesture.
  // This toast tells the user to tap once anywhere to enable voice.
  if (!voiceToastShown && authOk) {
    showAppToast("Tap once to enable voice calls.");
    voiceToastShown = true;
  }

  pickBoxEl.textContent = "None";

  if (authOk) {
    if (lobbyControls) lobbyControls.classList.remove("hidden");
    await loadGameAndRender().catch((e) => {
      subtitle.textContent = "Game error";
      setLobbyConnectHint("generic");
      setHint(`Failed to load game: ${e.message}`);
    });
  }

  if (themeToggle) {
    themeToggle.addEventListener("click", () => {
      const light = document.body.classList.toggle("theme-light");
      themeToggle.textContent = light ? "🌙" : "☀";
      themeToggle.setAttribute("aria-label", light ? "Switch to dark theme" : "Switch to light theme");
      themeToggle.title = light ? "Dark mode" : "Light mode";
    });
  }

  const appToastClose = document.getElementById("appToastClose");
  if (appToastClose) {
    appToastClose.addEventListener("click", () => hideAppToast());
  }

  setInterval(() => {
    if (!initData || !authOk) return;
    loadGameAndRender().catch(() => {});
  }, 2000);

  setInterval(() => {
    if (!initData || !authOk || !lastGameForUi) return;
    if (lastGameForUi.status !== "lobby") return;
    const g = lastGameForUi;
    const yp =
      g.your_bet && g.your_bet.picked_numbers && g.your_bet.picked_numbers.length
        ? Number(g.your_bet.picked_numbers[0])
        : null;
    const hb = !!(g.your_bet && g.your_bet.picked_numbers && g.your_bet.picked_numbers.length);
    const sec = getLobbyPickSecondsRemaining(g);
    const minPlayers = Number(g.min_players_to_start) || 2;
    const pc = Number(g.players_count) || 0;
    updateLobbyStrip(g, yp, hb);
    updateLobbyCountdownBanner(g);
    updateLobbyPlayersBanner(g, pc, minPlayers);
    if (sec != null && sec > 0) {
      rushPickEndArmedGameId = null;
    }
    if (sec === 0 && g.status === "lobby" && rushPickEndArmedGameId !== g.game_id) {
      rushPickEndArmedGameId = g.game_id;
      if (pc >= minPlayers) showBoardTransitionOverlay();
      scheduleRushLobbyPoll();
    }
  }, 1000);

  claimBingoBtn.addEventListener("click", async () => {
    try {
      const data = await apiFetch(`/games/${lastGameId}/claim-bingo`, { method: "POST", body: JSON.stringify({}) });
      setHint("BINGO! Checking…");
      await loadBalance().catch(() => {});
      if (data.winner_modal) {
        showBingoWinnerModalFromClaim(data);
      }
      await loadGameAndRender().catch(() => {});
    } catch (e) {
      const msg = e && e.message ? e.message : "BINGO failed";
      setHint(msg);
      // Show a toast popup so the player clearly sees the "blocked" reason.
      showAppToast(msg);
      await loadGameAndRender().catch(() => {});
    }
  });

  winnerCloseBtn.addEventListener("click", () => {
    dismissWinnerModalAndRefresh();
  });

  if (startParam && authOk) {
    const h = statusHintEl.textContent || "";
    setHint(h ? `${h} • Ref: ${startParam}` : `Ref: ${startParam}`);
  }
}

main().catch((e) => {
  setHint(e.message);
});
