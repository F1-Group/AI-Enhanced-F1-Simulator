import json
import platform
import queue
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

_LATENCY_DIR = Path(__file__).resolve().parent.parent.parent / "tests" / "integration"
if str(_LATENCY_DIR) not in sys.path:
    sys.path.insert(0, str(_LATENCY_DIR))

from latency_logger import log_event

try:
    import pygame
except ImportError:
    pygame = None

COOLDOWN_SECONDS = 4.0
SPEECH_TIMEOUT_SECONDS = 30.0

BUILTIN_ALERTS = {
    "brake_now": {
        "message": "Brake now",
        "priority": "urgent",
        "interrupt": True,
    },
    "off_track": {
        "message": "You are off track",
        "priority": "high",
        "interrupt": False,
    },
    "wrong_way": {
        "message": "Wrong way. Turn around.",
        "priority": "high",
        "interrupt": True,
    },
    "shift_up": {
        "message": "Shift up.",
        "priority": "high",
        "interrupt": True,
    },
    "shift_down": {
        "message": "Shift down.",
        "priority": "high",
        "interrupt": True,
    },
}

PRIORITY_ORDER = {
    "urgent": 0,
    "high": 1,
    "normal": 2,
    "low": 3,
    "slow": 4,
}
PRIORITY_LABELS = {v: k for k, v in PRIORITY_ORDER.items()}


class AudioManager:
    """Priority-based audio manager supporting clean cross-platform English TTS."""

    def __init__(self, cooldown_seconds=COOLDOWN_SECONDS):
        self.project_root = Path(__file__).resolve().parent.parent.parent
        self.cooldown_seconds = cooldown_seconds
        self._pygame_available = self._init_pygame()
        
        # Cross-platform environment detection
        self.os_type = platform.system()
        self.active_voice_id = None
        self._say_command = shutil.which("say")
        self.tts_engine = None

        # Environment configuration initialization
        if self.os_type == "Windows":
            try:
                import pyttsx3
                self.tts_engine = pyttsx3.init()
                voices = self.tts_engine.getProperty('voices')
                
                # Match native English voice packs (e.g., Zira, David)
                for voice in voices:
                    if "EN" in voice.id.upper() or "ENGLISH" in voice.name.upper():
                        self.active_voice_id = voice.id
                        break
                        
                if self.active_voice_id:
                    self.tts_engine.setProperty('voice', self.active_voice_id)
                else:
                    print("Windows Voice Alert: Native English TTS pack not found. Falling back to default.")
            except ImportError:
                print("Dependency Error: pyttsx3 missing. Execute 'pip install pyttsx3' on Windows.")
        # macOS
        elif self.os_type == "Darwin" and self._say_command:
            # Prioritize standard crisp English voice models
            available_mac_voices = ["Samantha", "Daniel", "Alex"]
            self.active_voice_id = "Samantha"
            try:
                result = subprocess.run([self._say_command, "-v", "?"], capture_output=True, text=True, check=False)
                for candidate in available_mac_voices:
                    if candidate in result.stdout:
                        self.active_voice_id = candidate
                        break
            except Exception:
                pass

        self._sounds = {}
        self._last_played = {}

        self._audio_queue = queue.PriorityQueue()
        self._counter = 0
        self._lock = threading.Lock()
        self._running = True

        # Handle to whatever speech subprocess/engine is CURRENTLY playing,
        # so stop_all() (called from _enqueue()'s interrupt path, on a
        # different thread than _audio_loop) can actually kill it. Without
        # this, interrupt=True only cleared items still WAITING in queue -
        # an already-playing utterance had no way to be preempted at all.
        self._current_speech_proc = None
        self._speech_proc_lock = threading.Lock()

        self._worker = threading.Thread(target=self._audio_loop, daemon=True)
        self._worker.start()

    def _init_pygame(self):
        if pygame is None:
            print("pygame is not installed; using speech fallback for audio alerts.")
            return False
        try:
            pygame.mixer.init()
            return True
        except pygame.error as error:
            print(f"pygame mixer unavailable; using speech fallback: {error}")
            return False

    def _message_for_tag(self, tag):
        if tag in BUILTIN_ALERTS:
            return BUILTIN_ALERTS[tag]["message"]
        return tag.replace("_", " ")

    def _message_for_error(self, error):
        tag = error.get("tag")
        if tag in BUILTIN_ALERTS:
            return BUILTIN_ALERTS[tag]["message"]
        return error.get("coaching_hint") or error.get("message") or (tag or "Driving alert").replace("_", " ")

    def _resolve_priority(self, priority):
        return PRIORITY_ORDER.get(priority, PRIORITY_ORDER["normal"])

    def _clear_queue(self, reason=None, caused_by=None):
        """Drain the queue. When `reason` is given (e.g. "interrupt"), also
        log exactly what got purged and whether each item was still within
        its 2.5s staleness budget at the moment it was killed - "still_valid"
        items are audio that would otherwise have played fine, wiped only
        because an interrupt event arrived. That's the concrete signal for
        "did off_track's interrupt=True wrongly delete other valid queued
        content", instead of having to listen for a dropped line by ear."""
        purged = []
        while not self._audio_queue.empty():
            try:
                item = self._audio_queue.get_nowait()
                self._audio_queue.task_done()
                purged.append(item)
            except queue.Empty:
                break

        if reason:
            now = time.monotonic()
            purged_info = []
            for (_, _, mode, _payload, description, created_time, log_key) in purged:
                age = now - created_time
                purged_info.append({
                    "description": description,
                    "mode": mode,
                    "age_s": round(age, 3),
                    "still_valid": age <= 2.5,
                    "log_key": str(log_key),
                })
            for info in purged_info:
                tag = "STILL_VALID (wrongly killed)" if info["still_valid"] else "already_stale"
                print(f"[Audio Queue] {reason} purge: \"{info['description']}\" "
                      f"age={info['age_s']:.2f}s -> {tag}")
            # Log even when nothing was purged (queue was already empty) -
            # that's a meaningful data point too: it means this interrupt
            # never got a chance to test collateral damage, as distinct from
            # an interrupt never having fired at all.
            log_event(f"audio_{reason}_purge", key=caused_by if caused_by is not None else now,
                      detail=json.dumps({"purged_count": len(purged_info), "purged": purged_info}))
        return purged

    # Introduce a global timestamp and expiration mechanism during Enqueue or Play.
    def _enqueue(self, payload, description, priority="normal", interrupt=False, mode="sound", latency_key=None):
        # Forcefully bind a creation timestamp when creating the event - still
        # used for the queue-staleness check below, independent of the
        # latency-log key.
        created_time = time.monotonic()
        # Log under the caller's correlation key (e.g. ai_core's
        # session_id:tag:lap_number) when given, so this stage joins up with
        # the published/received/guardrail_done stages upstream. Callers that
        # don't have one (play()/play_error()/play_sound()) keep the old
        # created_time-keyed behaviour.
        log_key = latency_key if latency_key is not None else created_time
        log_event("queued_for_voice", key=log_key, detail=description)
        log_event("audio_enqueued", key=log_key, detail=json.dumps({
            "description": description,
            "priority": priority,
            "interrupt": interrupt,
            "mode": mode,
            "queue_len_before": self._audio_queue.qsize(),
        }))

        if interrupt:
            self.stop_all()
            self._clear_queue(reason="interrupt", caused_by=log_key)

        with self._lock:
            self._counter += 1
            # Package created_time into the data structure.
            item = (self._resolve_priority(priority), self._counter, mode, payload, description, created_time, log_key)
            self._audio_queue.put(item)
        return True

    def _enqueue_speech(self, text, description, priority="normal", interrupt=False, latency_key=None):
        clean_text = " ".join(str(text).split())
        if not clean_text:
            return False
        return self._enqueue(clean_text, description, priority=priority, interrupt=interrupt, mode="speech", latency_key=latency_key)

    def play_text(self, text, priority="normal", interrupt=False, cooldown_key=None, latency_key=None):
        """Queue generated coaching text without creating a temporary file.

        latency_key: optional correlation key (e.g. "session_id:tag:lap_number")
        used to log the queued_for_voice/voice_start stages so they join up
        with the published/received/guardrail_done stages logged upstream by
        live_coach.py/ai_core.py under the same key.
        """
        clean_text = " ".join(str(text).split())
        if not clean_text:
            return False
        if cooldown_key:
            now = time.monotonic()
            with self._lock:
                if now - self._last_played.get(cooldown_key, 0) < self.cooldown_seconds:
                    return False
                self._last_played[cooldown_key] = now
        return self._enqueue_speech(
            clean_text,
            "AI Coaching Speech",
            priority=priority,
            interrupt=interrupt,
            latency_key=latency_key,
        )

    def _speak_text(self, text):
        if self.os_type == "Darwin" and self._say_command:
            proc = None
            try:
                proc = subprocess.Popen(
                    [self._say_command, "-v", self.active_voice_id, text],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                with self._speech_proc_lock:
                    self._current_speech_proc = proc
                try:
                    # SPEECH_TIMEOUT_SECONDS as a hard ceiling even with no
                    # interrupt at all - previously defined but never
                    # actually applied, so a hung/oversized utterance could
                    # block the channel indefinitely.
                    proc.wait(timeout=SPEECH_TIMEOUT_SECONDS)
                except subprocess.TimeoutExpired:
                    print(f"[Audio Warning] TTS exceeded {SPEECH_TIMEOUT_SECONDS}s; killing it.")
                    proc.kill()
                    proc.wait()
                return True
            except Exception as error:
                print(f"[Audio Error] macOS TTS failed: {error}")
                return False
            finally:
                with self._speech_proc_lock:
                    if self._current_speech_proc is proc:
                        self._current_speech_proc = None
        elif self.os_type == "Windows" and self.tts_engine:
            try:
                with self._speech_proc_lock:
                    self._current_speech_proc = self.tts_engine
                self.tts_engine.say(text)
                self.tts_engine.runAndWait()
                return True
            except Exception as error:
                print(f"Windows TTS execution failed: {error}")
                return False
            finally:
                with self._speech_proc_lock:
                    if self._current_speech_proc is self.tts_engine:
                        self._current_speech_proc = None

        print(f"Speech fallback platform unavailable. Alert text: {text}")
        return False

    def _audio_loop(self):
        while self._running:
            try:
                priority_weight, _, mode, payload, description, created_time, log_key = self._audio_queue.get(timeout=0.1)
                log_event("voice_start", key=log_key, detail=json.dumps({
                    "description": description,
                    "priority": PRIORITY_LABELS.get(priority_weight, "?"),
                    "wait_s": round(time.monotonic() - created_time, 3),
                }))
            except queue.Empty:
                continue

            # Drop and skip the event if the system is already shut down.
            if not self._running:
                self._audio_queue.task_done()
                break

            # Drop the event if it has been in the queue for more than 2.5 seconds.
            age = time.monotonic() - created_time
            if age > 2.5:
                print(f"[Timeout Dropped] {description} is too old ({age:.2f}s old), skipping.")
                log_event("audio_dropped_stale", key=log_key,
                          detail=json.dumps({"description": description, "age_s": round(age, 3)}))
                self._audio_queue.task_done()
                continue

            try:
                if mode == "speech":
                    log_desc = description if description in BUILTIN_ALERTS else "AI Coaching Speech"
                    print(f"Speaking audio: {log_desc}")

                    if self._running:
                        speak_start = time.monotonic()
                        self._speak_text(payload)
                        log_event("voice_done", key=log_key, detail=json.dumps({
                            "description": description,
                            "duration_s": round(time.monotonic() - speak_start, 3),
                        }))
                else:
                    if self._running and self._pygame_available:
                        channel = payload.play()
                        print(f"Playing audio: {description}")

                        if channel:
                            while channel.get_busy() and self._running:
                                time.sleep(0.05)
                            if not self._running:
                                self.stop_all()

            except Exception as error:
                print(f"Audio playback failed for {description}: {error}")
            finally:
                self._audio_queue.task_done()

    def play(self, tag, priority=None, interrupt=None):
        """Play a predefined audio clip or string directly by tag."""
        mapping = BUILTIN_ALERTS.get(tag)
        if mapping is None:
            mapping = {"priority": "normal", "interrupt": False, "message": self._message_for_tag(tag)}

        priority = priority or mapping.get("priority", "normal")
        interrupt = interrupt if interrupt is not None else mapping.get("interrupt", False)

        now = time.time()
        if now - self._last_played.get(tag, 0) < self.cooldown_seconds:
            return False
        self._last_played[tag] = now

        return self._enqueue_speech(
            mapping.get("message") or self._message_for_tag(tag),
            tag,
            priority=priority,
            interrupt=interrupt,
        )

    def play_sound(self, file_path, priority="slow", interrupt=False, fallback_text=None):
        """Play a WAV file, or fall back to native speech."""
        audio_path = Path(file_path)
        if not audio_path.is_absolute():
            audio_path = self.project_root / audio_path

        if self._pygame_available and audio_path.exists():
            try:
                sound = pygame.mixer.Sound(str(audio_path))
                return self._enqueue(sound, str(audio_path), priority=priority, interrupt=interrupt)
            except Exception as error:
                print(f"Audio file playback failed for {audio_path}: {error}")
        else:
            print(f"Audio file unavailable: {audio_path}")

        if fallback_text:
            return self._enqueue_speech(fallback_text, str(audio_path), priority=priority, interrupt=interrupt)
        return False

    def _cooldown_key_for_error(self, error):
        """Cooldown key for a slow/fast-layer error dict.

        Includes both the error type and the corner (e.g. "late_braking_Turn 1")
        so the same issue firing at different corners does not suppress each
        other under one shared per-type cooldown.
        """
        error_type = error.get("type")
        corner = error.get("corner")
        if error_type and corner:
            return f"{error_type}_{corner}"
        return error.get("audio_key") or error.get("tag") or error.get("audio_file")

    def play_error(self, error):
        """Handle incoming error dictionaries cleanly without templates."""
        tag = error.get("tag")
        audio_file = error.get("audio_file")
        priority = error.get("priority", "normal")
        interrupt = error.get("interrupt", False)

        if tag and tag in BUILTIN_ALERTS:
            return self.play(tag, priority=priority, interrupt=interrupt)

        if audio_file:
            cooldown_key = self._cooldown_key_for_error(error)
            now = time.time()
            if now - self._last_played.get(cooldown_key, 0) < self.cooldown_seconds:
                return False
            self._last_played[cooldown_key] = now
            return self.play_sound(
                audio_file,
                priority=priority,
                interrupt=interrupt,
                fallback_text=self._message_for_error(error),
            )

        fallback_text = self._message_for_error(error)
        if fallback_text:
            cooldown_key = self._cooldown_key_for_error(error) or fallback_text
            now = time.time()
            if now - self._last_played.get(cooldown_key, 0) < self.cooldown_seconds:
                return False
            self._last_played[cooldown_key] = now
            return self._enqueue_speech(fallback_text, cooldown_key, priority=priority, interrupt=interrupt)

        print(f"Invalid error packet structural footprint: {error}")
        return False

    def wait_until_idle(self, timeout=None):
        deadline = None if timeout is None else time.time() + timeout
        while self._running:
            mixer_busy = self._pygame_available and pygame.mixer.get_busy()
            if self._audio_queue.unfinished_tasks == 0 and not mixer_busy:
                return True
            if deadline is not None and time.time() >= deadline:
                return False
            time.sleep(0.1)
        return False

    def stop_all(self):
        if self._pygame_available:
            pygame.mixer.stop()

        # Kill whatever is CURRENTLY speaking, not just what's still queued.
        # Previously this only stopped pygame sounds - an interrupt=True
        # enqueue cleared the waiting queue but had no way to touch an
        # already-playing `say` subprocess, so a long utterance blocked any
        # higher-priority alert that arrived mid-speech until it finished on
        # its own (confirmed via analyze_audio_queue.py's in-flight-blocking
        # check). _speak_text() runs on the audio worker thread; this method
        # is called from whichever thread is enqueuing the interrupt, so the
        # process handle is guarded by _speech_proc_lock.
        with self._speech_proc_lock:
            proc = self._current_speech_proc
        if proc is None:
            return
        if self.os_type == "Darwin" and hasattr(proc, "poll"):
            if proc.poll() is None:
                try:
                    proc.terminate()
                except Exception as error:
                    print(f"[Audio Warning] Could not terminate in-flight TTS: {error}")
        elif self.os_type == "Windows" and proc is self.tts_engine:
            try:
                self.tts_engine.stop()
            except Exception as error:
                print(f"[Audio Warning] Could not stop in-flight TTS: {error}")

    def shutdown(self):
        """Terminate audio systems immediately and scrub remaining voice jobs."""
        print("[Audio] Initiating emergency forced shutdown...")
        self._running = False
        self.stop_all()
        self._clear_queue()
        
        if self.os_type == "Darwin" and self._say_command:
            try:
                subprocess.run(["killall", "say"], capture_output=True, check=False)
                print("[Audio] Terminated active background 'say' instances.")
            except Exception as e:
                print(f"[Audio Warning] Unable to signal process shutdown: {e}")

        if self._pygame_available:
            try:
                pygame.mixer.quit()
                self._pygame_available = False
            except Exception as e:
                print(f"[Audio Warning] Error wrapping down pygame engine: {e}")
                
        print("[Audio] Audio manager successfully forced to shut up.")


if __name__ == "__main__":
    audio_manager = AudioManager()
    audio_manager.play_text("System architecture running smoothly.", priority="slow")
    time.sleep(2)
    audio_manager.play("brake_now", priority="urgent", interrupt=True)
    time.sleep(2)
    audio_manager.shutdown()
