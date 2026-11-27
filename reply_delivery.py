"""Bounded speech prefetch and readable captions; no model/translation calls."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import re
import threading
from time import perf_counter
import textwrap
import unicodedata

import config
from controls import TurnCancelled
from text_to_speech import SpeechSynthesisError


class SegmentSpeechError(SpeechSynthesisError):
    def __init__(self, index, message):
        super().__init__(message)
        self.index = index


@dataclass
class Generation:
    audio: bytes | None
    seconds: float
    error: Exception | None = None
    generated: bool = False


@dataclass(frozen=True)
class SpeechPiece:
    parent: int
    text: str
    first: bool
    last: bool
    error: str | None = None


def check_japanese_speech(text):
    """Catch obvious script mismatches while allowing Latin names in Japanese."""
    normalized = unicodedata.normalize("NFKC", text)
    if (any("CYRILLIC" in unicodedata.name(char, "") for char in normalized)
            or not re.search(r"[ぁ-ゖァ-ヺ㐀-䶿一-鿿]", normalized)):
        raise ValueError("The reply's Japanese field was not suitable for Japanese speech. Showing subtitles instead.")


def split_speech_text(text, limit):
    """Split Japanese audio work only; never infer English sentence alignment."""
    if type(limit) is not int or limit <= 0:
        raise ValueError("Japanese speech-piece size must be a positive integer.")
    remaining = re.sub(r"\s+", " ", text).strip()
    pieces = []
    while len(remaining) > limit:
        window = remaining[:limit]
        sentences = [match.end() for match in re.finditer(r'[。！？!?][」』）”"]*', window)]
        clauses = [match.end() for match in re.finditer(r'[、，,;；:：]|\s+', window)]
        cut = sentences[-1] if sentences and sentences[-1] >= limit // 3 else clauses[-1] if clauses else limit
        if cut == limit and len(remaining) - cut < max(2, limit // 4):
            # Avoid a final punctuation-only / one-character engine request.
            cut = max(1, len(remaining) // 2)
        # Back up rather than cut a Latin word/quest-name token in half.
        if re.match(r"[A-Za-z0-9_]", remaining[cut - 1]) and re.match(r"[A-Za-z0-9_]", remaining[cut]):
            while cut > 0 and re.match(r"[A-Za-z0-9_]", remaining[cut - 1]):
                cut -= 1
            if cut == 0:
                raise ValueError("A single unbroken name exceeds the speech-piece size; its text remains available.")
        pieces.append(remaining[:cut])
        remaining = remaining[cut:]
    if remaining:
        pieces.append(remaining)
    return pieces


def speech_plan(segments):
    plan = []
    for parent, part in enumerate(segments):
        try:
            check_japanese_speech(part["ja"])
            fragments = split_speech_text(part["ja"], config.REPLY_CHUNK_JA_CHARS)
        except ValueError as error:
            # Fail only when this parent is reached; valid earlier pairs can play.
            plan.append(SpeechPiece(parent, "", True, True, str(error)))
            continue
        for index, text in enumerate(fragments):
            plan.append(SpeechPiece(parent, text, index == 0, index == len(fragments) - 1))
    return plan


def speak_segments(segments, *, synthesize, play, caption, control, wait, row, first_ready,
                   event=None, caption_language="en"):
    """Bounded Japanese audio pieces, with captions paired at the model-part level."""
    plan = speech_plan(segments)
    row["speech_chunks"] = len(plan)
    stopped = threading.Event()
    futures = []
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="Akane speech part")

    log = event or (lambda kind, **data: None)

    def generate(index, piece):
        control.check()
        if stopped.is_set():
            raise TurnCancelled()
        if piece.error:
            return Generation(None, 0.0, SpeechSynthesisError(piece.error))
        started = perf_counter()
        log("tts_start", part=piece.parent + 1, speech_piece=index + 1)
        outcome = "error"
        try:
            audio = synthesize(piece.text)
            outcome = "generated"
            return Generation(audio, perf_counter() - started, generated=True)
        except TurnCancelled:
            raise
        except Exception as error:
            return Generation(None, perf_counter() - started, error)
        finally:
            log("tts_end", part=piece.parent + 1, speech_piece=index + 1, status=outcome, elapsed_seconds=perf_counter() - started,
                cancelled=control.cancel.is_set() or stopped.is_set())

    def submit(index):
        control.check()
        future = pool.submit(generate, index, plan[index])
        futures.append(future)
        return future

    try:
        current = submit(0)
        parent_playback, parent_shown = 0.0, False
        for index, piece in enumerate(plan):
            control.phase("synthesizing")
            result = current.result()
            control.check()
            if result.error is not None:
                raise SegmentSpeechError(piece.parent, str(result.error))
            if index == 0:
                row["first_tts_seconds"] = result.seconds
                first_ready()
            following = None
            if config.TTS_PREFETCH_NEXT and index + 1 < len(plan):
                following = submit(index + 1)
            control.phase("speaking")
            if piece.first:
                parent_playback = 0.0
                # Keep the complete parent translation while its Japanese pieces
                # play. Never pair guessed translation fragments with Japanese cuts.
                parent_shown = control.show_caption(caption, segments[piece.parent][caption_language])
                if parent_shown:
                    row["subtitle_pages"] += 1
            started = perf_counter()
            playback_status = "error"
            log("playback_start", part=piece.parent + 1, speech_piece=index + 1)
            try:
                completed = play(result.audio, control)
                playback_status = "stopped" if completed is False or control.cancel.is_set() else "completed"
            except TurnCancelled:
                playback_status = "stopped"
                raise
            except RuntimeError as error:
                raise SegmentSpeechError(piece.parent, str(error)) from error
            finally:
                result.audio = None  # Futures keep timing metadata, not every earlier WAV.
                playback_seconds = perf_counter() - started
                row["playback_seconds"] += playback_seconds
                parent_playback += playback_seconds
                log("playback_end", part=piece.parent + 1, speech_piece=index + 1,
                    status=playback_status, elapsed_seconds=playback_seconds)
            if completed is False:
                raise TurnCancelled()
            control.check()
            row["spoken_chunks"] += 1
            if piece.last and parent_shown:
                wait(max(0.0, config.SUBTITLE_MIN_SECONDS - parent_playback))
            if index + 1 < len(plan):
                current = following if following is not None else submit(index + 1)
    finally:
        stopped.set()
        for future in futures:
            future.cancel()
        # Keep this turn busy until its one in-flight request finishes/times out.
        # No next turn or late callback can consume abandoned audio.
        pool.shutdown(wait=True, cancel_futures=True)
        for future in futures:
            if future.cancelled():
                continue
            try:
                result = future.result()
            except Exception:
                continue
            row["tts_seconds"] += result.seconds
            if result.generated:
                row["generated_chunks"] += 1
            result.audio = None


def show_text_pages(segments, *, caption, control, wait, row, caption_language="en"):
    """Page the selected translation when speech is unavailable; keep the last page."""
    pages = []
    for part in segments:
        pages.extend(textwrap.wrap(part[caption_language], width=config.REPLY_CHUNK_EN_CHARS,
                                   break_long_words=True, break_on_hyphens=False))
    for index, page in enumerate(pages):
        control.phase("captioning")
        if control.show_caption(caption, page):
            row["subtitle_pages"] += 1
        if index + 1 < len(pages):
            rate = config.SUBTITLE_READING_CHARS_PER_SECOND
            reading = len(page) / rate if rate > 0 else 0.0
            wait(max(config.SUBTITLE_MIN_SECONDS, reading))
