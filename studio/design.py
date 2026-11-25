"""Voice design: turn a few choices into a clear written description of the voice."""
from __future__ import annotations

AGES = ["", "young adult", "30-year-old", "middle-aged", "50-year-old", "65-year-old", "elderly"]
GENDERS = ["", "male", "female"]
ACCENTS = ["", "American", "British", "Australian", "Irish", "Indian", "neutral"]
VOICES = ["", "deep", "warm", "soft", "bright", "husky", "smooth", "gravelly"]
PACES = ["", "slow", "steady", "brisk"]
STYLES = ["", "documentary narration", "audiobook storytelling", "news reading", "friendly conversation",
          "dramatic trailer voice", "calm meditation guide", "energetic advertisement"]
MOODS = ["", "calm", "authoritative", "friendly", "mysterious", "confident", "gentle", "serious"]
EXAMPLES = [
    "A 65-year-old British male, deep voice, slow documentary narration, calm and authoritative.",
    "A young American woman, bright and friendly, brisk pace, like a cheerful podcast host.",
    "A warm middle-aged female storyteller with a soft voice, slow and gentle, for bedtime stories.",
]


def compose(age="", gender="", accent="", voice="", pace="", style="", mood="") -> str:
    """'A 65-year-old British male, deep voice, slow documentary narration, calm and authoritative.'"""
    who = " ".join(x for x in (age, accent, gender) if x).strip()
    if age and not gender:
        who += " speaker"
    parts = []
    if who:
        parts.append(("An " if who[0].lower() in "aeiou" else "A ") + who)
    elif any((voice, pace, style, mood)):
        parts.append("A voice")
    if voice:
        parts.append(f"{voice} voice")
    tail = " ".join(x for x in (pace, style) if x)
    if tail:
        parts.append(tail)
    if mood:
        parts.append(mood if " and " in mood else f"{mood}")
    if not parts:
        return ""
    text = ", ".join(parts)
    return text[0].upper() + text[1:] + "."


def valid_description(text: str) -> str:
    text = " ".join(text.split())
    if len(text) < 8:
        raise ValueError("Describe the voice in a few words, for example: a calm middle-aged British man.")
    if len(text) > 600:
        raise ValueError("Keep the description under 600 characters.")
    return text


# Choices shown as one-click chips: (label, phrase used in the description)
CHOICES = {
    "Age": [("Young", "young adult"), ("Adult", "30-year-old"), ("Middle-aged", "middle-aged"), ("Senior", "65-year-old")],
    "Gender": [("Male", "male"), ("Female", "female")],
    "Accent": [("American", "American"), ("British", "British"), ("Australian", "Australian"), ("Indian", "Indian"), ("Neutral", "neutral")],
    "Tone": [("Deep", "deep"), ("Warm", "warm"), ("Soft", "soft"), ("Bright", "bright"), ("Husky", "husky"), ("Smooth", "smooth")],
    "Pace": [("Slow", "slow"), ("Steady", "steady"), ("Brisk", "brisk")],
    "Mood": [("Calm", "calm"), ("Authoritative", "calm and authoritative"), ("Friendly", "friendly"), ("Serious", "serious"),
             ("Gentle", "gentle"), ("Mysterious", "mysterious")],
}
# Ready-made starting points: name -> (choices, use)
TEMPLATES = {
    "Documentary narrator": ({"Age": "Senior", "Gender": "Male", "Accent": "British", "Tone": "Deep", "Pace": "Slow", "Mood": "Authoritative"}, "documentary narration"),
    "Warm storyteller": ({"Age": "Middle-aged", "Gender": "Female", "Tone": "Warm", "Pace": "Slow", "Mood": "Gentle"}, "audiobook storytelling"),
    "News anchor": ({"Age": "Adult", "Accent": "American", "Tone": "Smooth", "Pace": "Steady", "Mood": "Serious"}, "news reading"),
    "Podcast host": ({"Age": "Young", "Accent": "American", "Tone": "Bright", "Pace": "Brisk", "Mood": "Friendly"}, "friendly conversation"),
    "Calm guide": ({"Age": "Adult", "Gender": "Female", "Tone": "Soft", "Pace": "Slow", "Mood": "Calm"}, "calm meditation guide"),
    "Trailer voice": ({"Age": "Middle-aged", "Gender": "Male", "Tone": "Deep", "Pace": "Steady", "Mood": "Mysterious"}, "dramatic trailer voice"),
}


def phrase(group: str, label: str) -> str:
    return next((p for l, p in CHOICES.get(group, []) if l == label), "")


def compose_from_chips(selected: dict, use: str = "") -> str:
    """selected: {'Age': 'Senior', ...} with labels; use: free phrase such as 'documentary narration'."""
    return compose(age=phrase("Age", selected.get("Age", "")), gender=phrase("Gender", selected.get("Gender", "")),
                   accent=phrase("Accent", selected.get("Accent", "")), voice=phrase("Tone", selected.get("Tone", "")),
                   pace=phrase("Pace", selected.get("Pace", "")), style=use, mood=phrase("Mood", selected.get("Mood", "")))
