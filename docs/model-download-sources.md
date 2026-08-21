# Model Download Sources

The launcher catalog uses public first-party repositories and direct assets. Model weights remain outside the repository and are downloaded into the project workspace after the user selects them.

## Sources

| Component | Source type | Official source |
| --- | --- | --- |
| GPT-SoVITS runtime | GitHub branch archive | https://github.com/RVC-Boss/GPT-SoVITS |
| GPT-SoVITS pretrained weights | Hugging Face file resolve URLs | https://huggingface.co/lj1995/GPT-SoVITS |
| VoxCPM2 runtime | GitHub branch archive | https://github.com/OpenBMB/VoxCPM |
| VoxCPM2 weights | Hugging Face file resolve URLs | https://huggingface.co/openbmb/VoxCPM2 |
| IndexTTS2 runtime | GitHub branch archive | https://github.com/index-tts/index-tts |
| IndexTTS2 weights | Hugging Face file resolve URLs | https://huggingface.co/IndexTeam/IndexTTS-2 |
| RVC runtime | GitHub branch archive | https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI |
| FFmpeg | Pinned public archive | https://ffmpeg.org/ |
| Sherpa ONNX | Official project and release pages | https://github.com/k2-fsa/sherpa-onnx |

## Integrity

The SHA-256 values for the configured large weight files were calculated from the corresponding files already present in the project workspace and are recorded in `config/model_catalog.json`. The launcher verifies the downloaded file before moving it into its final target.

GitHub branch archives do not have a stable content hash because the branch can change. They are used only for runtime source packages. A future release should pin these entries to a Git commit archive and record its hash before publishing a reproducible bundle.

The current Sherpa ONNX directory contains model packages whose official distribution is not represented by one stable archive URL in the project runtime. Its catalog entry therefore keeps the official project page as the source of truth and leaves the direct URL configurable through `config/model_catalog.local.json`.

## Licensing

The launcher only exposes public source links. Users remain responsible for reviewing the license and redistribution terms of each runtime, model, and voice asset before sharing a downloaded bundle.
