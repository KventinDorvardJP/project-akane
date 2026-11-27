# Shared turn processing for terminal and hotkey modes. One caller owns history.
from time import perf_counter, sleep
import re

import config
from backend_errors import BackendError
from controls import TurnControl, TurnCancelled
from conversation import parse_reply_segments, with_japanese_reference, with_wow_reference
from memory_commands import is_memory_command, memory_command
from metrics import append_timing
from source_output import write_sources
from reply_delivery import SegmentSpeechError, show_text_pages, speak_segments


class TurnProcessor:
    def __init__(self, root, system_prompt, reply_function, *, tts_enabled,
                 synthesize, play, write_caption, sleep_function=sleep, event_callback=None, session_log=None):
        self.root = root
        self.reply_function = reply_function
        self.tts_enabled = tts_enabled
        self.synthesize, self.play = synthesize, play
        self.write_caption, self.sleep = write_caption, sleep_function
        self.messages = [{"role": "system", "content": system_prompt}]
        self.event_callback = event_callback
        self.session_log = session_log
        self.active_log_turn = None
        self._log_warning_shown = False
        self.memory = None
        self.memory_warning = None

    def handle_memory_command(self, text, *, busy=False):
        if not is_memory_command(text):
            return False
        session_id = self.session_log.session_id if self.session_log and self.session_log.active else None
        result = ("Finish or stop the current turn before changing memory." if busy
                  else memory_command(self.memory, text, source_session=session_id))
        print(result)
        self.emit("memory", text=result)
        return True

    def log(self, kind, *, turn_id=None, **payload):
        if self.session_log is None:
            return None
        event = self.session_log.record(kind, turn_id=turn_id or self.active_log_turn, **payload)
        if self.session_log.error and not self._log_warning_shown:
            # Set the guard before notice(), which itself is an observed event.
            self._log_warning_shown = True
            self.notice(f"Session transcript unavailable: {self.session_log.error}", "warning")
        return event

    def begin_log_turn(self, mode, japanese_context=None):
        self.active_log_turn = self.session_log.begin_turn(mode, japanese_context) if self.session_log else None
        return self.active_log_turn

    def end_log_turn(self, turn_id, status):
        self.log("turn_end", turn_id=turn_id, status=status)
        if self.active_log_turn == turn_id:
            self.active_log_turn = None

    def mark_recording(self):
        event = self.session_log.mark(turn_id=self.active_log_turn) if self.session_log else None
        if event:
            self.notice(f"Recording marker {event['data']['number']} — {event['timestamp_utc']} "
                        f"(+{event['elapsed_seconds']:.3f}s).")
            return True
        self.notice("Session transcript is off or unavailable. Enable it in Settings and restart.", "warning")
        return False

    def emit(self, kind, **payload):
        if kind in {"user", "reply", "transcription", "metrics", "notice", "reset"}:
            self.log(kind, **payload)
        # GUI observers only enqueue data. Worker threads never touch Tk widgets.
        if self.event_callback:
            try:
                self.event_callback(kind, **payload)
            except Exception as error:
                print(f"Display event unavailable: {error}")

    def notice(self, message, level="info"):
        print(message)
        self.emit("notice", message=message, level=level)

    def update_sources(self, payload=None):
        try:
            write_sources(self.root, payload)
            self.emit("sources", **(payload or {"en": "", "sources": []}))
            return True
        except OSError as error:
            self.notice(f"Source output unavailable: {error}", "warning")
            return False

    def update_subtitle(self, text, *, clear_sources=True):
        if not text and clear_sources:
            self.update_sources()
        try:
            self.write_caption(self.root / "runtime" / "subtitle_en.txt", text)
            self.log("subtitle", text=text, language=config.SUBTITLE_LANGUAGE)
            return True
        except OSError as error:
            self.notice(f"Subtitle output unavailable: {error}", "warning")
            return False

    def reset(self):
        self.messages[:] = self.messages[:1]
        self.update_subtitle("")
        self.emit("reset")
        self.notice("Conversation history reset.")

    def respond(self, text, *, input_mode="typed", stt_seconds=0.0,
                response_started=None, control=None, japanese_context=None, turn_id=None, wow_context=None):
        controlled = control is not None
        control = control or TurnControl()
        turn_id = turn_id or control.turn_id or self.begin_log_turn(input_mode, japanese_context)
        self.active_log_turn = turn_id
        control.turn_id = turn_id
        response_started = perf_counter() if response_started is None else response_started
        row = {
            "input_mode": input_mode,
            "stt_backend": config.STT_BACKEND if input_mode != "typed" else "none",
            "reply_backend": getattr(self.reply_function, "__module__", type(self.reply_function).__name__),
            "status": "reply_unavailable", "stt_seconds": stt_seconds,
            "llm_seconds": 0.0, "tts_seconds": 0.0, "ready_seconds": 0.0,
            "playback_seconds": 0.0, "ja_characters": 0, "en_characters": 0,
            "reply_chunks": 0, "generated_chunks": 0, "spoken_chunks": 0, "subtitle_pages": 0,
            "first_tts_seconds": 0.0, "turn_seconds": 0.0,
            "context_seconds": control.context_seconds,
            "speech_chunks": 0,
        }
        pending_user, committed, requested = False, False, False
        try:
            control.phase("thinking")
            self.update_subtitle("")
            if re.match(r"^/search\b", text.strip(), re.I) and not getattr(self.reply_function, "supports_web_search", False):
                raise BackendError("/search needs the OpenAI conversation backend; local Ollama has no search tool.")
            context_tools = getattr(self.reply_function, "supports_context_tools", False) is True
            content = text if context_tools else with_wow_reference(with_japanese_reference(text, japanese_context), wow_context)
            self.messages.append({"role": "user", "content": content})
            pending_user = True
            self.emit("user", text=text, mode=input_mode, japanese_context=japanese_context,
                      wow_context=wow_context, stt_seconds=stt_seconds)
            self.log("llm_start", turn_id=turn_id)
            llm_started = perf_counter()
            try:
                requested = True
                if context_tools:
                    memory_options = {"memory": self.memory} if self.memory is not None else {}
                    reply = self.reply_function.reply_with_context(
                        self.messages, wow_context=wow_context, japanese_context=japanese_context,
                        control=control, log=self.log, **memory_options)
                else:
                    reply = self.reply_function(self.messages)
            finally:
                row["llm_seconds"] = perf_counter() - llm_started
                self.log("llm_end", turn_id=turn_id, elapsed_seconds=row["llm_seconds"])
            control.check()  # A stopped/paused HTTP result must never become speech.
            segments = parse_reply_segments(reply)
            ja_reply = "".join(part["ja"] for part in segments)
            caption_language = config.SUBTITLE_LANGUAGE
            translation = " ".join(part[caption_language] for part in segments)
            row["reply_chunks"] = len(segments)
            self.messages.append({"role": "assistant", "content": reply})
            committed = True
            while len(self.messages) > 1 + config.MAX_HISTORY_TURNS * 2:
                del self.messages[1:3]
            row["ja_characters"] = len(ja_reply)
            if caption_language == "en":
                row["en_characters"] = len(translation)
            print(f"LLM time: {row['llm_seconds']:.2f} seconds")
            print(f"Akane (JA): {ja_reply}")
            print(f"Akane ({caption_language.upper()}): {translation}")
            sources = getattr(self.reply_function, "last_sources", [])
            sources = sources if isinstance(sources, list) else []
            display = {"ja": ja_reply, caption_language: translation, "sources": sources, "segments": segments}
            control.show_caption(lambda payload: self.emit("reply", **payload), display)
            if isinstance(sources, list) and sources:
                # The same lock used for captions protects source publication
                # against pause/stop clearing. Cancelled replies cannot revive it.
                control.show_caption(self.update_sources, {caption_language: translation, "sources": sources})
                for number, source in enumerate(sources, 1):
                    print(f"Source {number} ({source['kind']}): {source['title']} — {source['url']}")
                print(f"Clickable sources: {self.root / 'runtime' / 'sources.html'}")

            def wait(seconds):
                if seconds > 0:
                    if controlled:
                        control.cancel.wait(seconds)
                    else:
                        self.sleep(seconds)
                control.check()

            def first_ready():
                row["ready_seconds"] = perf_counter() - response_started
                print(f"First speech ready (including STT): {row['ready_seconds']:.2f} seconds")

            if self.tts_enabled:
                try:
                    speak_segments(segments, synthesize=self.synthesize, play=self.play,
                                   caption=self.update_subtitle, control=control, wait=wait,
                                   row=row, first_ready=first_ready, caption_language=caption_language,
                                   event=lambda kind, **data: self.log(kind, turn_id=turn_id, **data))
                    row["status"] = "ok"
                    self.update_subtitle("", clear_sources=False)
                except SegmentSpeechError as error:
                    control.check()
                    row["status"] = "speech_unavailable"
                    if row["spoken_chunks"] == 0:
                        row["ready_seconds"] = perf_counter() - response_started
                    self.notice(f"Speech unavailable: {error}", "warning")
                    show_text_pages(segments[error.index:], caption=self.update_subtitle, control=control,
                                    wait=wait, row=row, caption_language=caption_language)
            else:
                row["status"] = "tts_disabled"
                row["ready_seconds"] = perf_counter() - response_started
                show_text_pages(segments, caption=self.update_subtitle, control=control, wait=wait,
                                row=row, caption_language=caption_language)
            return True
        except TurnCancelled:
            row["status"] = "cancelled"
            self.log("turn_cancelled", turn_id=turn_id)
            self.update_subtitle("")
            self.notice("Turn stopped.")
            return False
        except (BackendError, ValueError, TypeError) as error:
            self.log("reply_error", turn_id=turn_id, error_type=type(error).__name__, message=str(error))
            self.notice(f"Reply unavailable: {error}", "error")
            return False
        except Exception as error:
            row["status"] = "unexpected_error"
            self.log("turn_error", turn_id=turn_id, error_type=type(error).__name__, message=str(error))
            raise
        finally:
            row["turn_seconds"] = perf_counter() - response_started
            # Retain a valid visible reply if audio fails/stops after it was committed.
            # A cancelled/failed request before commitment rolls back its user entry.
            if pending_user and not committed:
                self.messages.pop()
            # A turn cancelled before the call must not copy the prior turn's charge.
            usage = getattr(self.reply_function, "last_usage", {}) if requested else {}
            if isinstance(usage, dict):
                row.update({
                    "llm_provider": usage.get("provider", ""),
                    "llm_model": usage.get("model", ""),
                    **{key: usage.get(key) for key in (
                        "input_tokens", "cached_input_tokens", "output_tokens",
                        "reasoning_tokens", "estimated_api_cost_usd", "web_search_calls",
                        "search_usage_source", "estimated_search_cost_usd", "estimated_text_cost_usd",
                        "provider_requests", "local_tool_calls")},
                })
                if usage.get("provider") == "openai":
                    print(f"OpenAI tokens: input={usage.get('input_tokens')}, output={usage.get('output_tokens')}")
                    if usage.get("web_search_calls"):
                        print(f"Web search requests: {usage['web_search_calls']} (estimate includes search fees)")
                    if usage.get("estimated_api_cost_usd") is not None:
                        print(f"Estimated conversation API cost: ${usage['estimated_api_cost_usd']:.6f}")
            try:
                append_timing(self.root / "runtime" / "timings.csv", row)
            except (OSError, ValueError) as error:
                self.notice(f"Timing output unavailable: {error}", "warning")
            self.emit("metrics", row=dict(row),
                      session_cost=getattr(self.reply_function, "estimated_session_usd", None),
                      cost_unknown=getattr(self.reply_function, "unaccounted_usage", False))
            self.end_log_turn(turn_id, row["status"])
