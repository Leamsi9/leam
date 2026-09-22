# Optional local speech worker

This is an optional English speech adapter. Moonshine recognizes microphone PCM;
Pocket synthesizes text with installed stock English voices (Alba by default). Input and output choices are
independent. Browser speech remains the default. The main conversational model and
its data handling are unchanged. Model inference never receives tool authority.

Use an isolated Python3.12 environment outside application source. Install CPU Torch
first from its official wheel index, then the pinned dependencies:

```sh
python3 -m venv ~/.local/share/leam-next/voice/venv
~/.local/share/leam-next/voice/venv/bin/pip install --index-url https://download.pytorch.org/whl/cpu 'torch==2.8.0+cpu'
~/.local/share/leam-next/voice/venv/bin/pip install -r scripts/voice/requirements.lock
~/.local/share/leam-next/voice/venv/bin/python scripts/voice/setup_models.py --directory ~/.local/share/leam-next/voice
```

Setup downloads the English Moonshine small-streaming model and the official
**without-voice-cloning** Pocket English checkpoint plus 19 precomputed stock
English embeddings. Their names, upstream revision and SHA-256 digests are pinned
in `leam_api/voice_catalog.py`; files must match before they are advertised.
Cosette and Jean are excluded because their source recordings are non-commercial. Hugging Face requests are anonymous; this script cannot accept gated
account terms. It does not start a service, capture audio or clone a voice. The
private model manifest records upstream versions/checksums. The worker token is
created0600 and never printed. Do not put that directory or its manifest in Git.

Existing installations can add only the small stock embeddings without downloading
or replacing the main models, config, or worker token:

```sh
~/.local/share/leam-next/voice/venv/bin/python scripts/voice/setup_models.py --directory ~/.local/share/leam-next/voice --stock-voices-only
```

Stage assets before deployment, preserve the previous manifest, and activate the new
manifest/source with an idle-worker restart. The running worker keeps its current
inventory until restart. Existing Alba-only manifests remain supported. Status
reports only installed files that match the pinned digest; absent voices are
rejected before synthesis. One selected embedding stays loaded at a time, and
switching voices uses the same native serialization as synthesis.

From the selected immutable Leam release, the operator can run:

```sh
~/.local/share/leam-next/voice/venv/bin/python -m leam_api.voice_worker --directory ~/.local/share/leam-next/voice
```

Root/deployment owner installs and supervises the service, using the exact source
release as its working directory. It binds only127.0.0.1:46440; do not expose that
port through a reverse proxy. Leam's authenticated same-origin `/api/voice/*`
routes are the browser boundary. The main app reads the private worker token from
`~/.local/share/leam-next/voice/worker-token`. No new public credential is needed.
Restarting or stopping this optional worker leaves browser speech available.

Models load before listening; status fails honestly until ready. The worker uses
two CPU affinity slots and two Torch threads, with offline model loading.
`MOONSHINE_ORT_SINGLE_THREAD=1` is set before model loading: Moonshine's
ONNX Runtime sessions otherwise create host-wide thread pools despite main-thread
affinity, exhausting a two-CPU service quota. This flag limits ORT intra/inter-op
threads to one and sequential execution; Torch remains independently limited to two. The
synthetic setup benchmark used about1.28GiB peak resident memory; an operator may
set a suitable service memory limit with headroom. This is not a universal bound.
No raw audio file, cache or request-body log is created. Native text/debug-waveform
logging and Pocket debug/save flags are disabled. Keep HTTP access logs disabled.

Microphone PCM is mono16kHz float32, at most1second/request and90seconds/capture;
invalid samples, sequence reuse, expired capture and another active owner fail.
Input capture expires after120seconds even without further requests. TTS accepts
at most300characters and emits bounded PCM frames, at most45seconds of audio.
Cancellation stops client playback immediately. Pocket3.1 cannot safely interrupt
its internal native threads: the worker drains the short in-flight phrase while
keeping model serialization, then admits new inference. There is no hard native
compute deadline; a stuck native call requires restarting the optional worker.
Do not describe CPU affinity or output-byte limits as a hard inference timeout.

## Upstream attribution

- [Moonshine](https://github.com/moonshine-ai/moonshine), code and selected English
  model: MIT. Other language models are not installed or implied by this license.
- [Pocket TTS](https://github.com/kyutai-labs/pocket-tts), code: MIT.
- [Pocket without voice cloning](https://huggingface.co/kyutai/pocket-tts-without-voice-cloning),
  model weights: CC BY4.0, Kyutai. Exact model revision is pinned in setup.
- [Alba MacKenna stock voice](https://huggingface.co/kyutai/tts-voices#alba-mackenna),
  provided through Kyutai: CC BY4.0. Only its supplied precomputed embedding is used.
- Additional stock voices supplied by [Kyutai](https://huggingface.co/kyutai/tts-voices):
  Anna, Azelma, Charles, Eponine, Eve, Fantine, George, Jane, Mary, Michael, Paul
  and Vera use the [VCTK dataset](https://datashare.ed.ac.uk/handle/10283/3443),
  CC BY 4.0. Bill Boerst, Caro Davy, Peter Yearsley and Stuart Bell come from
  Kyutai's Voice-Zero selection, CC0. Javert and Marius come from Kyutai's
  voice donations, CC0. The manifest preserves the exact upstream embedding
  provenance. Only supplied embeddings are used; no recording is cloned locally.
- PyTorch and other package terms remain their respective licenses; the isolated
  environment preserves distribution metadata. No package or model license is
  replaced by Leam's source licensing.

CPU tests use synthetic generated speech without storing audio. Phone microphone
quality, mobile Safari audio unlock, network conditions and full conversation
latency require separate actual-device acceptance before changing defaults.
