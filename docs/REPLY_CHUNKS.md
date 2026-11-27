# Paired speech and subtitle parts

Akane requests ordered Japanese/English pairs from the conversation model:

```json
{"segments": [
  {"ja": "まずは、この言葉の意味からね。", "en": "Let's start with what this word means."},
  {"ja": "前の台詞によって、主語が変わるよ。", "en": "The subject depends on the preceding dialogue."}
]}
```

The model chooses paired clauses/sentences. Each JA part goes to the selected TTS
engine in bounded local audio pieces; its complete matching EN part becomes the
caption before that part's first playback and remains through all of its pieces.
Akane does not independently split both translations and assume alignment, or
make another model/translation request for each audio piece.

## Delivery

After one complete validated model reply arrives, the GUI shows the whole answer
and history stores the paired JSON once. Akane synthesizes the first short JA
piece, shows its parent EN caption, and plays it. It can prepare one next piece during
current playback: only one synthesis runs at a time, and at most current/next
WAVs are held. Earlier audio is released instead of accumulating an entire reply.

Captions advance with speech and clear after the final part/minimum hold. The
last spoken caption remains readable while waiting for the next audio. The fitted
background still follows `subtitle_en.txt`; sources remain separate. Both VOICEVOX
and GPT-SoVITS use this path. GPT-SoVITS's internal splitting is a separate setting.

This is segmented TTS **after full model generation**, not streamed partial JSON.
It can reduce first-speech delay for long answers, but latency/naturalness still
need live checks. More TTS requests add overhead and may change prosody. One-part
prefetch overlaps work with playback without guaranteeing gapless speech.

## Settings

New controls appear automatically in Settings (Subtitles and Session):

| Setting | Default | Meaning |
| --- | ---: | --- |
| `REPLY_CHUNK_JA_CHARS` | 80 | Maximum JA characters per local audio piece; generation target per pair |
| `REPLY_CHUNK_EN_CHARS` | 160 | EN generation target per pair; text-only page width |
| `REPLY_MAX_CHUNKS` | 12 | Maximum parts per answer; normal replies should use 1–3 |
| `TTS_PREFETCH_NEXT` | true | Prepare one part ahead |
| `SUBTITLE_READING_CHARS_PER_SECOND` | 22 | Text-only reading hold; zero disables this extra hold |

Targets include spaces/punctuation and are requested in the generation instructions.
The schema retains the paired structure and part-count bound, but avoids hard
per-string character caps: a live follow-up ended at exactly 80 Japanese characters
inside a quest name while returning completed, valid JSON. That is evidence of a
character-boundary failure, rather than a confirmed output-token exhaustion. The
model is asked to use another complete pair instead of stopping inside a name or
quotation. This prompt/schema change still needs live quality acceptance.

Japanese audio is locally subdivided at sentence/clause/whitespace boundaries,
with a character-boundary fallback for continuous Japanese and word-aware breaks
for Latin tokens. Speech whitespace is normalized; no words are discarded.
The complete parent English translation is shown throughout its Japanese audio
pieces, without guessed cross-language phrase alignment. English caption length
never blocks Japanese synthesis. If the model exceeds the English target, the
full caption can exceed it too; check wrapping/height in the actual OBS viewport.
The GUI retains the exact complete model text.

A single unbreakable Latin token longer than the configured piece limit keeps a
text-only fallback at that parent instead of cutting the name. This does not
prevent earlier valid pairs from playing. No model retry is made. Character counts
are not rendered pixel measurements: smaller OBS sources/larger fonts may need
lower limits than the defaults for the documented 1200 × 420 source. Output-token
limits apply to the complete reply. Settings use the existing Save/restart flow.

## Cancellation and failures

Stop/Pause clear captions and discard pending output; later parts are not queued.
An already sent TTS request may finish before the turn becomes idle, but its audio
is discarded. Exit/reset retain the existing cleanup rules.

If speech fails partway through, already spoken audio is not replayed. The failed
parent's complete English translation and later pairs use text pages because a
failed audio piece cannot be mapped to a guessed English fragment. The final page stays until the
next turn/clear command. Disabling speech also pages English. The GUI keeps the
whole valid answer throughout.

Legacy `{"ja":"...","en":"..."}` replies are accepted as one pair and use the
same bounded audio-piece path. Caption minimum holds apply once per parent pair,
not once per local audio piece. Akane does not invent alignment, silently truncate
content, or automatically make a paid retry.

## Timing records

For successful speech, `ready_seconds` now means first speech-ready time (including
STT where applicable), not full-answer TTS completion. It is readiness before the
playback call, not measured physical sound onset or OBS rendering. Old rows have
blank chunk fields to distinguish their previous meaning.

Fields: `reply_chunks` counts model pairs; `speech_chunks` counts planned local
audio pieces; `generated_chunks`/`spoken_chunks` count generated/played pieces.
The GUI uses speech-piece counts as its denominator. Old piece counts remain
blank after safe CSV migration, rather than being guessed. Session-log audio
events retain the parent `part` and a unique `speech_piece` index.

Other fields include `subtitle_pages`,
`first_tts_seconds`, `turn_seconds`. Cancelled output can have more generated than
spoken parts. `tts_seconds` sums generation work; `playback_seconds` sums playback
calls. They can overlap and must not be added to infer elapsed time. `turn_seconds`
includes holds and cleanup of pending synthesis. Old CSV formats migrate with a
backup. The explicit benchmark synthesizes per part and reports first-ready time;
it does not play audio or simulate prefetch/playback overlap.

Service-free tests cover schemas, ordered captions, full GUI/history preservation,
bounded prefetch, cancellation, fallback pages, holds, size checks and CSV migration.
They use fake engines/audio and an in-memory SDK transport. Live alignment, engine
latency, prosody, pronunciation and OBS fit remain manual acceptance checks.

Schema reference: [official OpenAI Structured Outputs documentation](https://developers.openai.com/api/docs/guides/structured-outputs).
