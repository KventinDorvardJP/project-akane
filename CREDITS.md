# Voice credits and source terms

Checked against the linked official sources on **2026-11-09**.

## Selected voice

Akane currently uses **VOICEVOX 冥鳴ひまり・ノーマル**, engine style ID **14**.
Include the following credit when publishing streams or recordings using that voice:

```text
VOICEVOX:冥鳴ひまり
```

The official engine resource terms give this credit format and allow commercial and
noncommercial use subject to the voice's terms. The voice owner's terms require
credit and permit using the voice for an original character. See the complete terms
for restrictions; this project does not grant rights to a character illustration or model.

- [Official VOICEVOX voice-library terms](https://github.com/VOICEVOX/voicevox_resource/blob/main/engine/README.md#冥鳴ひまり)
- [冥鳴ひまり official terms and FAQ](https://www.meimeihimari.com/terms-of-use)
- [VOICEVOX software terms](https://voicevox.hiroshiba.jp/term/)
- [Official voice page](https://voicevox.hiroshiba.jp/product/meimei_himari/)

For the test stream, put the credit in the stream/video description or a visible
credits area. Recheck the applicable terms when changing voices or publishing a new use.

## Other components

Ollama, the selected Qwen model, Faster-Whisper, the OpenAI SDK/API, and the Python
dependencies retain their own terms and licenses. They are installed separately;
VOICEVOX binaries and voice models are not bundled in this repository.

LunaTranslator is a separately installed application connected through its documented
original-text WebSocket API. Its code and game assets are not bundled. The `websockets`
client and other Python packages retain their own licenses.

Architecture reference: [Rayen's Project Riko](https://github.com/rayenfeng/riko_project).
This reference does not license Akane's original source or grant rights to third-party assets.
