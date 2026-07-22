from app.domain import CharacterTier, CharacterVoice, CharacterVoiceBible


def test_character_bible_keeps_identity_outside_director_segments() -> None:
    bible = CharacterVoiceBible(
        project_id="demo",
        source_text="input/demo.txt",
        characters=[
            CharacterVoice(
                character_id="narrator",
                display_name="旁白",
                confidence=1.0,
                importance=1.0,
                tier=CharacterTier.core,
            )
        ],
    )
    assert bible.characters[0].character_id == "narrator"
