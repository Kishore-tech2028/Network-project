"""
session_manager.py — Thread-safe quiz session logic
─────────────────────────────────────────────────────
Manages participants, scores, timed rounds, fairness
evaluation (EWMA latency), and real-time leaderboard.
Uses only stdlib; no Django dependency at runtime.
"""

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# ───────────────────────── Participant ─────────────────────────

@dataclass
class Participant:
    """Tracks a single player's state throughout the quiz."""

    name: str
    score: int = 0
    connected: bool = True
    ready: bool = False
    latency_ewma_ms: float = 120.0          # exponentially weighted moving avg
    answers_by_question: Dict[int, str] = field(default_factory=dict)
    last_answer_latency_ms: float = 0.0

    def update_latency(self, observed_ms: float) -> None:
        """Smooth latency estimate with EWMA (α = 0.25)."""
        alpha = 0.25
        self.latency_ewma_ms = alpha * observed_ms + (1 - alpha) * self.latency_ewma_ms
        self.last_answer_latency_ms = observed_ms

    @property
    def fairness_allowance_seconds(self) -> float:
        """Grace window after deadline — compensates for network latency."""
        return min(max(self.latency_ewma_ms / 2000.0, 0.05), 0.40)


# ───────────────────────── Session Manager ─────────────────────

class SessionManager:
    """
    Central quiz controller.

    • Loads questions from a JSON file
    • Manages rounds with deadlines
    • Accepts / rejects answers with fairness evaluation
    • Produces a real-time sorted leaderboard
    """

    QUESTION_SECONDS = 10       # seconds per question
    TRANSITION_SECONDS = 7      # pause between questions (allows round leaderboard)

    def __init__(self, questions_path: str) -> None:
        self._lock = threading.Lock()
        self.questions: List[dict] = self._load_questions(questions_path)
        self.participants: Dict[str, Participant] = {}

        # Quiz state
        self.started: bool = False
        self.finished: bool = False
        self.current_question_index: int = -1
        self.quiz_start_ts: float = 0.0
        self.question_start_ts: float = 0.0
        self.question_deadline_ts: float = 0.0
        self.host: Optional[str] = None

    # ── Question loading ──────────────────────────────────────

    @staticmethod
    def _load_questions(path: str) -> List[dict]:
        """Load and return questions list from a JSON file."""
        questions_file = Path(path)
        if not questions_file.exists():
            raise FileNotFoundError(f"Questions file not found: {path}")
        with questions_file.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
        questions = list(payload.get("questions", []))
        if not questions:
            raise ValueError("No questions found in the file.")
        return questions

    # ── Participant management ────────────────────────────────

    def add_participant(self, name: str, is_host: bool = False) -> str:
        """Register or reconnect a participant. Returns status message."""
        with self._lock:
            existing = self.participants.get(name)
            if existing:
                existing.connected = True
                if not is_host and not self.started:
                    existing.ready = True
                status = "reconnected"
            else:
                self.participants[name] = Participant(
                    name=name,
                    ready=(not self.started),
                )
                status = "joined"

            current_host = self.participants.get(self.host) if self.host else None
            host_disconnected = bool(current_host and not current_host.connected)
            if is_host and (self.host is None or self.host == name or host_disconnected):
                self.host = name
                host_participant = self.participants.get(name)
                if host_participant:
                    host_participant.ready = True
            return status

    def set_participant_ready(self, name: str, ready: bool = True) -> Tuple[bool, str]:
        """Set readiness for a participant (host is always ready)."""
        with self._lock:
            participant = self.participants.get(name)
            if not participant:
                return False, "unknown_participant"
            if self.host and name == self.host:
                participant.ready = True
                return True, "host_always_ready"

            participant.ready = bool(ready)
            return True, "ready_set" if participant.ready else "ready_cleared"

    def can_receive_questions(self, name: str) -> bool:
        """Whether this participant should receive live question payloads."""
        with self._lock:
            participant = self.participants.get(name)
            if not participant or not participant.connected:
                return False
            if self.host and name == self.host:
                return False
            return bool(participant.ready)

    def get_participants_snapshot(self) -> List[dict]:
        """Thread-safe participant roster for lobby/monitoring UIs."""
        with self._lock:
            participants = sorted(
                self.participants.values(),
                key=lambda p: p.name.lower(),
            )
            return [
                {
                    "name": p.name,
                    "connected": p.connected,
                    "ready": p.ready,
                    "score": p.score,
                    "is_host": p.name == self.host,
                }
                for p in participants
            ]

    def get_state_snapshot(self) -> dict:
        """Thread-safe snapshot of quiz state for client synchronization."""
        with self._lock:
            return {
                "host": self.host,
                "quiz_started": self.started,
                "quiz_finished": self.finished,
                "quiz_start_ts": self.quiz_start_ts,
                "current_question": self._public_question_unlocked(),
                "participants": [
                    {
                        "name": p.name,
                        "connected": p.connected,
                        "ready": p.ready,
                        "score": p.score,
                        "is_host": p.name == self.host,
                    }
                    for p in sorted(self.participants.values(), key=lambda p: p.name.lower())
                ],
            }

    def mark_disconnected(self, name: str) -> None:
        """Flag a participant as disconnected."""
        with self._lock:
            p = self.participants.get(name)
            if p:
                p.connected = False
            if self.host == name:
                self.host = None

    def remove_participant(self, name: str) -> bool:
        """Permanently remove a participant from the roster."""
        with self._lock:
            if name not in self.participants:
                return False
            del self.participants[name]
            if self.host == name:
                self.host = None
            return True

    def get_connected_count(self) -> int:
        """Number of currently connected participants."""
        with self._lock:
            return sum(
                1
                for name, participant in self.participants.items()
                if participant.connected and name != self.host
            )

    # ── Quiz control ──────────────────────────────────────────

    def start_quiz(self, by_user: str, delay_seconds: float = 5.0) -> Tuple[bool, str]:
        """Start the quiz. Only the host may start it."""
        with self._lock:
            if self.finished:
                return False, "quiz_already_finished"
            if self.started:
                return False, "quiz_already_started"
            if self.host and by_user != self.host:
                return False, "only_host_can_start"
            if not self.questions:
                return False, "no_questions"

            self.started = True
            self.current_question_index = -1
            self.quiz_start_ts = time.time() + delay_seconds
            return True, "quiz_starting_soon"

    def restart_quiz(self, by_user: str, delay_seconds: float = 5.0) -> Tuple[bool, str]:
        """Reset scores/state and schedule a fresh quiz countdown."""
        with self._lock:
            if self.host and by_user != self.host:
                return False, "only_host_can_restart"
            if not self.questions:
                return False, "no_questions"

            for participant in self.participants.values():
                participant.score = 0
                participant.answers_by_question.clear()
                participant.last_answer_latency_ms = 0.0
                participant.ready = participant.name == self.host

            self.started = True
            self.finished = False
            self.current_question_index = -1
            self.quiz_start_ts = time.time() + delay_seconds
            self.question_start_ts = 0.0
            self.question_deadline_ts = 0.0
            return True, "quiz_restarting_soon"

    def _open_current_question(self) -> None:
        """Set the start and deadline timestamps for the current round."""
        now = time.time()
        self.question_start_ts = now
        self.question_deadline_ts = now + self.QUESTION_SECONDS

    def advance_to_next_question(self) -> dict:
        """Move to the next question or finish the quiz. Returns state dict."""
        with self._lock:
            if not self.started or self.finished:
                return {"finished": self.finished}

            next_idx = self.current_question_index + 1
            if next_idx >= len(self.questions):
                self.finished = True
                return {"finished": True, "leaderboard": self._leaderboard_unlocked()}

            self.current_question_index = next_idx
            self._open_current_question()
            return {"finished": False, "question": self._public_question_unlocked()}

    # ── Question payloads ─────────────────────────────────────

    def get_current_question(self) -> dict:
        """Thread-safe public question payload."""
        with self._lock:
            return self._public_question_unlocked()

    def _public_question_unlocked(self) -> dict:
        """Build the question dict sent to clients (no lock)."""
        if not self.started or self.finished or self.current_question_index < 0:
            return {}
        q = self.questions[self.current_question_index]
        return {
            "index": self.current_question_index,
            "question": q.get("question", ""),
            "options": q.get("options", []),
            "started_at": self.question_start_ts,
            "deadline": self.question_deadline_ts,
            "duration": self.QUESTION_SECONDS,
            "total_questions": len(self.questions),
        }

    def get_correct_answer(self) -> Optional[str]:
        """Return the correct answer for the current question."""
        with self._lock:
            if 0 <= self.current_question_index < len(self.questions):
                return self.questions[self.current_question_index].get("answer")
            return None

    # ── Answer submission ─────────────────────────────────────

    def submit_answer(self, user: str, answer: str,
                      client_sent_ts: Optional[float] = None) -> dict:
        """
        Process an answer submission with fairness evaluation.

        Returns a result dict with accepted/rejected status,
        correctness, score, and latency info.
        """
        with self._lock:
            if not self.started:
                return {"accepted": False, "reason": "quiz_not_started"}
            if self.finished:
                return {"accepted": False, "reason": "quiz_finished"}

            participant = self.participants.get(user)
            if not participant:
                return {"accepted": False, "reason": "unknown_participant"}
            if self.host and user == self.host:
                return {"accepted": False, "reason": "host_monitor_only"}
            if not participant.ready:
                return {"accepted": False, "reason": "player_not_ready"}

            q_idx = self.current_question_index
            if q_idx in participant.answers_by_question:
                return {"accepted": False, "reason": "duplicate_answer"}

            # Latency measurement
            now = time.time()
            observed_latency_ms = 0.0
            if client_sent_ts is not None:
                observed_latency_ms = max(0.0, (now - client_sent_ts) * 1000.0)
                participant.update_latency(observed_latency_ms)

            # Fairness: allow a grace window based on estimated latency
            cutoff = self.question_deadline_ts + participant.fairness_allowance_seconds
            if now > cutoff:
                return {
                    "accepted": False,
                    "reason": "timeout",
                    "server_received": now,
                    "deadline": self.question_deadline_ts,
                    "fairness_allowance_s": participant.fairness_allowance_seconds,
                    "latency_ms": observed_latency_ms,
                }

            # Record and evaluate
            participant.answers_by_question[q_idx] = answer
            correct_answer = self.questions[q_idx].get("answer", "")
            correct = answer.strip() == str(correct_answer).strip()
            if correct:
                participant.score += 1

            return {
                "accepted": True,
                "latency_ms": round(observed_latency_ms, 2),
                "fairness_allowance_s": participant.fairness_allowance_seconds,
            }

    # ── Leaderboard ───────────────────────────────────────────

    def get_leaderboard(self) -> List[dict]:
        """Thread-safe leaderboard sorted by score ↓, latency ↑, name."""
        with self._lock:
            return self._leaderboard_unlocked()

    def _leaderboard_unlocked(self) -> List[dict]:
        """Build the leaderboard (no lock)."""
        players = [
            participant
            for participant in self.participants.values()
            if participant.name != self.host
        ]
        ranked = sorted(
            players,
            key=lambda p: (-p.score, p.latency_ewma_ms, p.name.lower()),
        )
        return [
            {
                "rank": idx + 1,
                "name": p.name,
                "score": p.score,
                "connected": p.connected,
                "latency_ms": round(p.latency_ewma_ms, 2),
            }
            for idx, p in enumerate(ranked)
        ]
