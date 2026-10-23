from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from ..Audio.ReferenceAudio import ReferenceAudio


class Context:
    def __init__(self):
        self.current_speaker: str = ''
        self.current_prompt_audio: Optional['ReferenceAudio'] = None


context: Context = Context()
