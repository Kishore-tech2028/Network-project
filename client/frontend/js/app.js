let ws;
let joinedUsername = "";
let currentHostName = "";
let currentQuestionIndex = -1;
let questionDuration = 10;
let timerInterval;
let manualLeave = false;

// DOM Elements
const screens = {
  join: document.getElementById("join-screen"),
  ready: document.getElementById("ready-screen"),
  waiting: document.getElementById("waiting-screen"),
  quiz: document.getElementById("quiz-screen"),
  finished: document.getElementById("finished-screen"),
};

function showScreen(screenId) {
  Object.values(screens).forEach((s) => s.classList.remove("active"));
  screens[screenId].classList.add("active");
}

function connect() {
  // Protocol relative WebSocket URL
  const wsProto = window.location.protocol === "https:" ? "wss:" : "ws:";
  const wsUrl = `${wsProto}//${window.location.host}/ws`;

  ws = new WebSocket(wsUrl);

  ws.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data);
      handleServerMessage(msg);
    } catch (e) {
      console.error("Invalid WS message:", e);
    }
  };

  ws.onclose = () => {
    if (!manualLeave) {
      document.getElementById("overlay-disconnected").classList.add("active");
      setTimeout(connect, 2000); // Reconnect loop
    }
  };

  ws.onopen = () => {
    document.getElementById("overlay-disconnected").classList.remove("active");
  };
}

// ──────────────────────────────────────────────────────────
//                   Message Handlers
// ──────────────────────────────────────────────────────────

function handleServerMessage(msg) {
  switch (msg.type) {
    case "welcome":
      handleWelcome(msg);
      break;
    case "question":
      handleNewQuestion(msg.payload);
      break;
    case "answer_result":
      handleAnswerResult(msg);
      break;
    case "question_closed":
      handleQuestionClosed(msg.payload);
      break;
    case "quiz_countdown":
      startSyncCountdown(msg.quiz_start_ts);
      break;
    case "quiz_finished":
      handleQuizFinished(msg.leaderboard);
      break;
    case "participants_update":
      handleParticipantsUpdate(msg);
      break;
    case "start_rejected":
      alert(`Could not start: ${msg.message}`);
      break;
    case "quiz_reset":
      handleQuizReset();
      break;
    case "ready_rejected":
      alert(`Could not set ready: ${msg.message}`);
      break;
    case "left_quiz":
      handleLeftQuiz();
      break;
  }
}

function showReadyGate(reason = "quiz_in_progress") {
  const readyText = document.getElementById("ready-text");
  if (readyText) {
    readyText.textContent =
      reason === "quiz_restarted"
        ? "Host restarted the quiz. Press Ready to join this run, or Leave to stay out."
        : "Quiz is in progress. Press Ready to join, or Leave to stay out.";
  }
  showScreen("ready");
}

function requestReady() {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  ws.send(JSON.stringify({ action: "set_ready", ready: true }));
}

function leaveQuiz() {
  manualLeave = true;
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    handleLeftQuiz();
    return;
  }
  ws.send(JSON.stringify({ action: "leave_quiz" }));
}

function handleLeftQuiz() {
  manualLeave = true;
  clearInterval(timerInterval);
  clearInterval(countdownInterval);
  currentQuestionIndex = -1;
  showScreen("join");
  document.getElementById("join-error").textContent = "";
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.close();
  }
}

function handleWelcome(msg) {
  joinedUsername = msg.username || joinedUsername;
  currentHostName = msg.host || currentHostName;

  document.getElementById("welcome-msg").textContent =
    `Welcome, ${msg.username}!`;

  updateParticipantsUI(msg.participant_list || [], msg.host || null);

  if (msg.requires_ready) {
    showReadyGate(msg.ready_reason || "quiz_in_progress");
    return;
  }

  if (msg.quiz_finished) {
    // Late joiner who missed entire quiz!
    showScreen("finished");
    renderLeaderboard(msg.participant_list || []);
  } else if (msg.quiz_started) {
    if (msg.current_question && Object.keys(msg.current_question).length > 0) {
      // Mid-game joiner
      handleNewQuestion(msg.current_question);
    } else {
      // Joined during the 5s countdown
      startSyncCountdown(msg.quiz_start_ts);
    }
  } else {
    // Pre-game waiting
    showScreen("waiting");
  }
}

let countdownInterval;

function startSyncCountdown(startTs) {
  if (!startTs || startTs <= 0) return;
  showScreen("waiting");

  const countText = document.getElementById("countdown-text");
  const loader = document.querySelector("#waiting-screen .loader");
  if (loader) {
    loader.style.display = "none";
  }

  clearInterval(countdownInterval);
  countdownInterval = setInterval(() => {
    const now = Date.now() / 1000.0;
    let left = Math.ceil(startTs - now);

    if (left > 0) {
      countText.textContent = `Quiz starting in ${left}...`;
    } else {
      clearInterval(countdownInterval);
      countText.textContent = "Starting now!";
    }
  }, 200);
}

function handleParticipantsUpdate(msg) {
  const participants = Array.isArray(msg.participants) ? msg.participants : [];
  const hostParticipant = participants.find(
    (participant) => participant.is_host,
  );
  const hostName = msg.host || hostParticipant?.name || null;
  currentHostName = hostName || currentHostName;
  updateParticipantsUI(participants, hostName);

  const selfParticipant = participants.find(
    (participant) => participant.name === joinedUsername,
  );
  const selfReady = Boolean(selfParticipant?.ready);
  const currentQuestion = msg.current_question || {};
  const hasCurrentQuestion = Object.keys(currentQuestion).length > 0;

  if (!msg.quiz_finished && msg.quiz_started && !selfReady) {
    showReadyGate(hasCurrentQuestion ? "quiz_in_progress" : "quiz_restarted");
    return;
  }

  if (!msg.quiz_finished && msg.quiz_started && hasCurrentQuestion) {
    if (
      !screens.quiz.classList.contains("active") ||
      currentQuestionIndex !== currentQuestion.index
    ) {
      handleNewQuestion(currentQuestion);
    }
  } else if (!msg.quiz_finished && msg.quiz_started && msg.quiz_start_ts) {
    startSyncCountdown(msg.quiz_start_ts);
  }
}

function updateParticipantsUI(participants, hostName) {
  renderParticipantsList("ready-participants", participants, hostName);
  renderParticipantsList("waiting-participants", participants, hostName);
  renderParticipantsList("quiz-participants", participants, hostName);
}

function renderParticipantsList(containerId, participants, hostName) {
  const container = document.getElementById(containerId);
  if (!container) return;

  container.innerHTML = "";
  if (!participants.length) {
    container.textContent = "No participants yet";
    return;
  }

  const sorted = [...participants].sort(
    (a, b) =>
      Number(b.score || 0) - Number(a.score || 0) ||
      String(a.name).localeCompare(String(b.name)),
  );
  sorted.forEach((p) => {
    const row = document.createElement("div");
    row.className = "participant-row";

    const hostBadge = p.is_host || p.name === hostName ? " 👑" : "";
    const state = p.connected ? "🟢" : "🔴";
    const readyBadge =
      p.is_host || p.name === hostName
        ? " • Host"
        : p.ready
          ? " • ✅ Ready"
          : " • ⏳ Not Ready";

    row.innerHTML = `
            <span class="left">${state} ${p.name}${hostBadge}${readyBadge}</span>
            <span class="score">${p.score ?? 0}</span>
        `;
    container.appendChild(row);
  });
}

function handleNewQuestion(payload) {
  showScreen("quiz");
  currentQuestionIndex = payload.index;
  questionDuration = payload.duration;

  // UI elements to update
  const titleEle = document.getElementById("question-title");
  const counterEle = document.getElementById("question-counter");
  const optionsContainer = document.getElementById("options-container");
  const timerFill = document.getElementById("timer-fill");

  // 1. Reset timer bar immediately without transition so it snaps to 100%
  timerFill.style.transition = "none";
  timerFill.style.width = "100%";

  // 2. Animate content out
  titleEle.classList.add("animated-content", "fade-out");
  optionsContainer.classList.add("animated-content", "fade-out");

  updateStatus("Reading question...", "#94a3b8");

  setTimeout(() => {
    // 3. Swap text and rebuild buttons safely inside the fixed container
    counterEle.textContent = `Question ${payload.index + 1} / ${payload.total_questions}`;
    titleEle.textContent = payload.question;

    optionsContainer.innerHTML = "";
    payload.options.forEach((opt) => {
      const btn = document.createElement("button");
      btn.className = "option-btn";
      btn.textContent = opt;
      btn.onclick = () => selectOption(btn, opt);
      optionsContainer.appendChild(btn);
    });

    // 4. Animate content back in
    titleEle.classList.remove("fade-out");
    optionsContainer.classList.remove("fade-out");

    // 5. Start timer countdown (Force reflow to enable CSS transition again)
    void timerFill.offsetWidth;
    timerFill.style.transition = `width ${questionDuration}s linear`;

    startTimer(questionDuration, payload.deadline);
  }, 200); // matches CSS transition time
}

function startTimer(duration, serverDeadlineTs) {
  clearInterval(timerInterval);

  const timerFill = document.getElementById("timer-fill");
  const timerText = document.getElementById("question-timer");

  const now = Date.now() / 1000.0;
  let left = Math.ceil(serverDeadlineTs - now);
  if (left < 0) left = 0;

  // Animate from exact percentage remaining
  timerFill.style.transition = "none";
  timerFill.style.width = `${(left / duration) * 100}%`;
  void timerFill.offsetWidth; // exact reflow
  timerFill.style.transition = `width ${left}s linear`;
  timerFill.style.width = "0%";

  timerText.textContent = `${left}s`;

  timerInterval = setInterval(() => {
    left--;
    if (left >= 0) {
      timerText.textContent = `${left}s`;
    } else {
      clearInterval(timerInterval);
      timerText.textContent = "0s";
      lockAllButtons();
    }
  }, 1000);
}

// ──────────────────────────────────────────────────────────
//                      Interactions
// ──────────────────────────────────────────────────────────

function selectOption(btn, answerStr) {
  // Prevent double clicking
  if (btn.disabled || document.querySelector(".option-btn.selected")) return;

  // Visual selection
  document.querySelectorAll(".option-btn").forEach((b) => {
    b.classList.remove("selected");
    b.disabled = true; // lock all
  });
  btn.classList.add("selected");
  btn.disabled = false; // keep selected active visually

  updateStatus("Answer submitted. Evaluating...", "#6366f1");

  // Send to web socket
  ws.send(
    JSON.stringify({
      action: "submit_answer",
      answer: answerStr,
      client_sent_ts: Date.now() / 1000.0,
    }),
  );
}

function handleAnswerResult(msg) {
  const selectedBtn = document.querySelector(".option-btn.selected");
  if (!selectedBtn) return;

  if (msg.accepted) {
    // Visual selection only, don't reveal if it's correct yet!
    updateStatus("Answer locked! Waiting for others...", "#6366f1");
  } else {
    selectedBtn.classList.remove("selected");
    updateStatus(`Rejected: ${msg.reason}`, "#ef4444");
    // re-enable if time remains
    document
      .querySelectorAll(".option-btn")
      .forEach((b) => (b.disabled = false));
  }
}

function handleQuestionClosed(payload) {
  clearInterval(timerInterval);
  document.getElementById("timer-fill").style.transition = "none";
  document.getElementById("timer-fill").style.width = "0%";

  lockAllButtons();

  // Reveal correct answer
  let gotItRight = false;
  document.querySelectorAll(".option-btn").forEach((b) => {
    if (b.textContent === payload.answer) {
      b.classList.add("correct");
      if (b.classList.contains("selected")) gotItRight = true;
    } else {
      if (b.classList.contains("selected")) b.classList.add("wrong");
      else b.style.opacity = "0.5";
    }
  });

  if (gotItRight) {
    updateStatus(`Correct! The answer was: ${payload.answer}`, "#10b981");
  } else {
    updateStatus(
      `Time's up! The correct answer was: ${payload.answer}`,
      "#ef4444",
    );
  }

  // Show leaderboard after a brief delay
  setTimeout(() => {
    // Only switch if we are still theoretically in the quiz screen (handles race conditions)
    if (
      Object.values(screens).find((s) => s.classList.contains("active")) ===
      screens.quiz
    ) {
      showScreen("finished");
      document.getElementById("finished-title").textContent =
        "Round Leaderboard";
      renderLeaderboard(payload.leaderboard);
    }
  }, 2000);
}

function handleQuizFinished(rankings) {
  showScreen("finished");
  document.getElementById("finished-title").textContent = "Quiz Finished! 🏆";
  renderLeaderboard(rankings);
}

function handleQuizReset() {
  clearInterval(timerInterval);
  clearInterval(countdownInterval);
  currentQuestionIndex = -1;
  showReadyGate("quiz_restarted");
}

function renderLeaderboard(rankings) {
  const container = document.getElementById("leaderboard-container");
  container.innerHTML = "";

  const filteredRankings = Array.isArray(rankings)
    ? rankings.filter((r) => r && r.name && r.name !== currentHostName)
    : [];

  filteredRankings.forEach((r, idx) => {
    const medal =
      idx === 0 ? "🥇" : idx === 1 ? "🥈" : idx === 2 ? "🥉" : `${idx + 1}.`;
    const div = document.createElement("div");
    div.className = "leaderboard-row";
    div.innerHTML = `
            <span><span style="display:inline-block;width:30px">${medal}</span> ${r.name}</span>
            <span class="score">${r.score}</span>
        `;
    container.appendChild(div);
  });
}

// ──────────────────────────────────────────────────────────
//                           Helpers
// ──────────────────────────────────────────────────────────

function lockAllButtons() {
  document.querySelectorAll(".option-btn").forEach((b) => (b.disabled = true));
}

function updateStatus(text, color) {
  const el = document.getElementById("status-msg");
  el.style.color = color;
  el.textContent = text;
}

// Init
document.getElementById("join-btn").addEventListener("click", () => {
  const name = document.getElementById("username-input").value.trim();
  if (!name) {
    document.getElementById("join-error").textContent = "Please enter a name";
    return;
  }

  manualLeave = false;
  joinedUsername = name;

  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(
      JSON.stringify({
        action: "join",
        username: name,
        host: false,
      }),
    );
  } else {
    document.getElementById("join-error").textContent =
      "Socket not connected yet.";
  }
});

// Boot WebSocket
connect();

document.getElementById("ready-btn").addEventListener("click", requestReady);
document.getElementById("leave-btn").addEventListener("click", leaveQuiz);
