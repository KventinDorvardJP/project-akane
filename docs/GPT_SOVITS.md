# Optional GPT-SoVITS speech

VOICEVOX remains the default. GPT-SoVITS is an independent TTS choice: it receives
Akane's Japanese reply and returns a complete WAV to the existing terminal or
interruptible GUI/hotkey playback path. English subtitles and conversation/STT
selection are unchanged.

## Supported interface

This connector targets the **official GPT-SoVITS inference Gradio WebUI** and its
named `/get_tts_wav` endpoint. It was tested with a running Gradio **4.24.0** server
on `http://127.0.0.1:9872`. Port **9874** in that setup is the management/training
UI; do not point Akane there. These ports are configurable in GPT-SoVITS.

The separate `api.py` / `api_v2.py` HTTP server on port 9880 is a different interface
and is not used by this adapter. Other forks/versions can change endpoint names,
parameters or localized language choices; those need separate verification.

Install/start GPT-SoVITS separately, with its own Python environment and models.
The optional client requirements do not install its engine, Torch, CUDA or weights
into Akane's venv:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-gpt-sovits.txt
```

Akane connects only when synthesizing with this backend. Default VOICEVOX runs do
not import the optional Gradio client or connect to GPT-SoVITS. Client analytics
are disabled, and the configured engine URL must be loopback HTTP.

Upstream: [GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS).

## Configure a reference voice

In Akane's **Settings…**, choose the engine in **Session** and configure the reference
file pickers/transcript in **GPT-SoVITS**. **Save & restart** applies an idle session's
saved choices; see [SETTINGS.md](SETTINGS.md). Manual JSON configuration also works.

Merge the following into the existing ignored `config.local.json`, preserving
your microphone and other settings. Replace paths and reference transcript with
your own actual files/text:

```json
{
  "GPT_SOVITS_URL": "http://127.0.0.1:9872",
  "GPT_SOVITS_REFERENCE_AUDIO": "audio_samples/reference.wav",
  "GPT_SOVITS_REFERENCE_TEXT": "The exact words spoken in your reference audio.",
  "GPT_SOVITS_AUX_REFERENCE_AUDIO": [],
  "GPT_SOVITS_PROMPT_LANGUAGE": "Japanese",
  "GPT_SOVITS_TEXT_LANGUAGE": "Japanese"
}
```

Relative paths resolve against Akane's project folder, regardless of your terminal's
working directory. The main reference needs its matching transcript and language;
the optional extra reference clips are used for tone averaging, without separate
transcripts. The tested WebUI asks for a main reference between 3 and 10 seconds.
The example transcript above is a placeholder, not Japanese reference material.

Akane uploads only the configured reference files to the local inference server
through the Gradio client. It does not upload a microphone recording as a voice
reference or use your selected STT/cloud conversation provider for synthesis.
Choose/load the desired GPT and SoVITS weights yourself in the inference UI.
Akane uses that server's current weights and does not change them automatically.

The user's tested v2 weight pair was:

```text
GPT_SoVITS/pretrained_models/gsv-v2final-pretrained/s1bert25hz-5kh-longer-epoch=12-step=369668.ckpt
GPT_SoVITS/pretrained_models/gsv-v2final-pretrained/s2G2333k.pth
```

## Run or save a test WAV

Use GPT-SoVITS for one Akane session:

```powershell
.\run.ps1 -Gui -Hotkeys -Overlay -LlmBackend openai -TtsBackend gpt_sovits
```

Direct Python switch: `main.py --tts-backend gpt_sovits`. The window identifies
the selected TTS engine. For regular VOICEVOX, omit the switch (with the default
configuration) or explicitly pass `-TtsBackend voicevox`. `-NoTts` still disables
speech entirely. To change the configured default later, set `TTS_BACKEND` in your
local overrides; the public default stays `"voicevox"`.

Generate a WAV without an LLM/STT request or audio playback:

```powershell
.\.venv\Scripts\python.exe tts_generate_sample.py --tts-backend gpt_sovits
```

Use `--text "こんにちは。"` for a custom sentence. Files are saved under the ignored
`runtime/tts_samples/` directory, with the backend and timestamp in their names.
`tts_volume_sample.py` remains an explicit VOICEVOX volume test regardless of the
configured backend. `VOICEVOX_VOLUME_SCALE` does not apply to GPT-SoVITS audio.

Optional settings in `config.py` / local JSON:

- `GPT_SOVITS_TEXT_SPLIT_METHOD`: defaults to the WebUI's `"Slice by every punct"`.
  This is engine-internal splitting. Akane's paired speech/subtitle parts are
  implemented separately in [REPLY_CHUNKS.md](REPLY_CHUNKS.md).
- `GPT_SOVITS_SPEED`: defaults to `1.0`; Akane accepts `0.6`–`1.65`.
- `GPT_SOVITS_TIMEOUT`: defaults to 120 seconds for queued generation, also used
  for transport operations. Initialization/uploads and queue execution are separate
  operations, so this is not a strict total-turn deadline.

## Failure, cancellation and files

Generation errors become the existing recoverable speech error: the valid reply
remains readable. There is no silent voice fallback. Stop/Pause still suppress
late audio/captions; stopping an Akane turn does not guarantee immediate cessation
of an already running GPU job in GPT-SoVITS. A queue timeout requests cancellation
on a best-effort basis.

Akane validates a complete, nonempty PCM WAV before playback. Temporary client
downloads are removed after conversion to WAV bytes, including on failures.
Original reference files are preserved. GPT-SoVITS/Gradio may retain their own
server-side upload/output caches; Akane does not manage that application's cache.
Only the explicit sample generator intentionally keeps a WAV in Akane's runtime.

Automated tests use a fake Gradio client, files, clocks/providers as appropriate;
they do not synthesize live audio or play sound. A separate authorized live check
on 2026-11-10 used the user's main and two extra Japanese references and the
user-confirmed loaded v2 weights. It produced a valid mono 32 kHz WAV of **4.94 s**
in **6.94 s**, including client connection/upload/generation/download overhead.
This is one connectivity check, not a warm benchmark or voice-quality acceptance.
The file was saved without playback; listening and OBS audio capture remain manual.
