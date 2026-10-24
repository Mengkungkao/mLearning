"""The 240x280 LCD screens: pure functions from state to a Pillow image.

mFruit OS look throughout (SDK status bar, lists, footer, toast, message).
``device.py`` decides which screen shows and passes the state in, so every
screen can be rendered and checked without a device (tests, ``--preview``).
Text is wrapped, shortened or scrolled to fit; nothing is drawn outside
the content area between the status bar and the footer.
"""

from __future__ import annotations

import math
from typing import Optional

from PIL import Image

from mfruit_sdk.ui import Canvas, Row, draw_list, footer, message, status_bar, text_field, toast
from mfruit_sdk.ui.theme import CONTENT_BOTTOM, CONTENT_TOP, MARGIN, SCREEN_W

from .. import state_machine as sm

TEXT_W = SCREEN_W - 2 * MARGIN
LINE_H = 19


def _hints(hints: list, armed: bool, release: str) -> list:
    return [("release", release)] if armed else [(g, label) for g, label, *_ in hints]


def list_screen(title: str, rows: list, selected: int, hints: list, status=None,
                armed: bool = False, release: str = "to open", note: str = "",
                top: int = CONTENT_TOP, header: Optional[str] = None) -> Image.Image:
    c = Canvas()
    status_bar(c, title, status)
    if header:
        c.text(MARGIN, top + 4, header, 12, "medium", c.theme.text_faint, max_width=TEXT_W)
        top += 24
    draw_list(c, rows, selected, top=top)
    footer(c, _hints(hints, armed, release))
    if note:
        toast(c, note)
    return c.image


# ---------------------------------------------------------------- lesson
STATE_STYLE = {
    # state -> (label, tone)
    sm.LISTENING: ("Listening…", "success"),
    sm.PRACTICE_WAITING: ("Your turn", "success"),
    sm.PROCESSING_SPEECH: ("Got it…", "warning"),
    sm.GENERATING_RESPONSE: ("Let me think…", "warning"),
    sm.SPEAKING: ("Speaking…", "accent"),
    sm.PAUSED: ("Paused", "muted"),
    sm.IDLE: ("Hold to talk", "muted"),
    sm.ERROR: ("Problem", "error"),
    sm.INITIALISING: ("Starting…", "muted"),
    sm.SHUTTING_DOWN: ("Closing…", "muted"),
}


def _tone(theme, tone: str):
    return {"success": theme.success, "warning": theme.warning, "accent": theme.accent,
            "error": theme.error}.get(tone, theme.text_faint)


def lesson_label(view, mic_off: bool) -> tuple:
    state = view.state
    label, tone = STATE_STYLE.get(state, (state.title(), "muted"))
    if state == sm.LISTENING and view.holding:
        label = "Release to send"
    elif state == sm.LISTENING and view.hearing:
        label = "Hearing you…"
    elif state == sm.IDLE and view.listening_mode == "hands_free":
        label, tone = ("Microphone off", "muted") if mic_off else ("Ready", "muted")
    elif state == sm.GENERATING_RESPONSE and view.finishing:
        label = "Summarising…"
    return label, tone


def _meter(c, levels: list, floor: float, y: int, colour) -> None:
    """Mirrored bars from recent microphone levels (dBFS)."""
    bars = 17
    gap = 4
    width = (TEXT_W - gap * (bars - 1)) // bars
    x = MARGIN + (TEXT_W - (bars * width + gap * (bars - 1))) // 2
    recent = (levels[-bars:] if levels else [])
    recent = [-90.0] * (bars - len(recent)) + list(recent)
    centre = y + 12
    for index, level in enumerate(recent):
        strength = max(0.0, min(1.0, (level - floor - 3) / 30.0))
        half = 2 + int(10 * strength)
        c.rounded((x, centre - half, x + width, centre + half), width // 2,
                  fill=colour if strength > 0.05 else c.theme.surface_hi)
        x += width + gap


def _spinner(c, x: int, y: int, phase: int, colour) -> None:
    c.spinner(x, y, 8, phase, colour)


def _split_long(c, word: str, size: int, weight: str, width: int) -> list:
    """A word wider than the line (a file path) in pieces, preferably after '/'."""
    pieces, current = [], ""
    for char in word:
        if current and c.text_width(current + char, size, weight) > width:
            cut = current.rfind("/") + 1
            if 0 < cut < len(current):
                pieces.append(current[:cut])
                current = current[cut:]
            else:
                pieces.append(current)
                current = ""
        current += char
    if current:
        pieces.append(current)
    return pieces


def _wrap(c, text: str, size: int, weight: str = "regular", width: int = TEXT_W,
          max_lines: int = 99) -> list:
    """Word wrap that also breaks words too long for a line (paths, URLs)."""
    if not text:
        return []
    lines = []
    for paragraph in str(text).split("\n"):
        line = ""
        for word in paragraph.split(" "):
            if c.text_width(word, size, weight) > width:
                if line:
                    lines.append(line)
                    line = ""
                parts = _split_long(c, word, size, weight, width)
                lines.extend(parts[:-1])
                line = parts[-1]
                continue
            candidate = f"{line} {word}".strip()
            if not line or c.text_width(candidate, size, weight) <= width:
                line = candidate
            else:
                lines.append(line)
                line = word
        lines.append(line)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = c.fit(lines[-1] + " …", size, weight, width)
    return lines


def _correction_card(c, correction, top: int, bottom: int) -> None:
    t = c.theme
    c.rounded((MARGIN - 6, top, SCREEN_W - MARGIN + 6, bottom), 12, fill=t.surface)
    y = top + 8
    left = MARGIN + 2
    width = TEXT_W - 8
    if correction.you_said:
        c.text(left, y, "You said", 11, "semibold", t.text_faint)
        y += 15
        for line in _wrap(c, correction.you_said, 13, "regular", width, 2):
            c.text(left, y, line, 13, "regular", t.text_muted)
            y += 17
        y += 3
    c.text(left, y, "Better", 11, "semibold", t.success)
    y += 15
    for line in _wrap(c, correction.better, 15, "semibold", width, 3):
        c.text(left, y, line, 15, "semibold", t.text)
        y += 19
    y += 3
    room = max(0, (bottom - 6 - y) // 16)
    for line in _wrap(c, correction.explanation, 12, "regular", width, room):
        c.text(left, y, line, 12, "regular", t.text_muted)
        y += 16


def _practice_card(c, target: str, result: str, top: int, bottom: int) -> None:
    t = c.theme
    c.rounded((MARGIN - 6, top, SCREEN_W - MARGIN + 6, bottom), 12, fill=t.surface,
              outline=t.accent_dim)
    y = top + 9
    c.text(MARGIN + 2, y, "Say this", 11, "semibold", t.accent)
    y += 17
    lines = _wrap(c, target, 17, "bold", TEXT_W - 8, 4)
    for line in lines:
        c.text(MARGIN + 2, y, line, 17, "bold", t.text)
        y += 22
    if result:
        c.text(MARGIN + 2, bottom - 22, result, 12, "medium", t.text_muted, max_width=TEXT_W - 8)


def _text_block(c, text: str, top: int, bottom: int, fraction: float = 0.0,
                colour=None, size: int = 15) -> None:
    lines = _wrap(c, text, size)
    visible = max(1, (bottom - top) // (size + 5))
    first = 0
    if len(lines) > visible:
        first = min(len(lines) - visible, int(round(fraction * (len(lines) - visible + 1))))
    y = top
    for line in lines[first:first + visible]:
        c.text(MARGIN, y, line, size, "regular", colour or c.theme.text)
        y += size + 5


def lesson(view, hints: list, status=None, levels: Optional[list] = None, floor: float = -60.0,
           progress: float = 0.0, phase: int = 0, mic_off: bool = False,
           typing: Optional[str] = None, note: str = "") -> Image.Image:
    c = Canvas()
    t = c.theme
    label, tone = lesson_label(view, mic_off)
    colour = _tone(t, tone)
    status_bar(c, view.page or "Lesson", status, dot=colour)
    y = CONTENT_TOP + 4
    # state banner: a light, the label and the microphone state
    busy = view.state in (sm.PROCESSING_SPEECH, sm.GENERATING_RESPONSE)
    if busy:
        _spinner(c, MARGIN + 8, y + 11, phase, colour)
    else:
        c.draw.ellipse((MARGIN + 2, y + 5, MARGIN + 14, y + 17), fill=colour)
    mic_text = "Mic on" if (view.mic_on and not mic_off and view.state in (sm.LISTENING, sm.PRACTICE_WAITING)) else "Mic off"
    pill_w = c.text_width(mic_text, 11, "semibold") + 12
    c.text(MARGIN + 22, y, label, 17, "bold", t.text, max_width=TEXT_W - 30 - pill_w)
    on = mic_text == "Mic on"
    c.pill(SCREEN_W - MARGIN, y + 2, mic_text, (255, 255, 255) if on else t.text_muted,
           t.success if on else t.surface_hi, align_right=True)
    y += 30

    card_top, card_bottom = y + 34, CONTENT_BOTTOM - 2
    state = view.state
    turn = view.turn
    if state in (sm.LISTENING, sm.PRACTICE_WAITING) and not view.holding and levels is not None:
        _meter(c, levels, floor, y + 2, colour)
    elif state == sm.LISTENING and view.holding:
        _meter(c, levels or [], floor, y + 2, colour)
    elif state == sm.SPEAKING:
        c.progress((MARGIN, y + 10, SCREEN_W - MARGIN, y + 16), progress, fg=t.accent)
    elif busy and view.last_transcript:
        c.text(MARGIN, y + 6, "You said", 11, "semibold", t.text_faint)
    elif state == sm.PAUSED:
        c.text(MARGIN, y + 6, "Microphone off · tap to continue", 13, "medium", t.text_muted,
               max_width=TEXT_W)
    elif state == sm.IDLE and view.listening_mode == "hands_free":
        c.text(MARGIN, y + 6, "Tap to listen, or hold to talk", 13, "medium", t.text_muted,
               max_width=TEXT_W)
    elif state == sm.IDLE:
        c.text(MARGIN, y + 6, "Hold the button while you speak", 13, "medium", t.text_muted,
               max_width=TEXT_W)

    if typing is not None:
        card_bottom = CONTENT_BOTTOM - 44
    if busy and view.last_transcript:
        _text_block(c, f"“{view.last_transcript}”", card_top - 12, card_bottom, colour=t.text_muted, size=14)
    elif state == sm.SPEAKING:
        _text_block(c, view.speaking_text, card_top - 12, card_bottom, progress)
    elif view.practice_target and state in (sm.PRACTICE_WAITING, sm.LISTENING, sm.IDLE, sm.PAUSED):
        _practice_card(c, view.practice_target, view.practice_result, card_top - 6, card_bottom)
    elif turn is not None and turn.corrections:
        _correction_card(c, turn.corrections[0], card_top - 6, card_bottom)
    elif turn is not None:
        _text_block(c, turn.reply_text, card_top - 12, card_bottom, 0.0, t.text_muted)
    if typing is not None:
        text_field(c, typing, CONTENT_BOTTOM - 38, "Type, then Enter")
    footer(c, [(g, label) for g, label, *_ in hints])
    if note:
        toast(c, note)
    return c.image


# --------------------------------------------------------------- reading
def _styles(t) -> dict:
    return {"title": (17, "bold", t.text), "label": (11, "semibold", t.text_faint),
            "body": (14, "regular", t.text), "muted": (13, "regular", t.text_muted),
            "good": (14, "semibold", t.success), "warn": (13, "medium", t.warning),
            "accent": (14, "semibold", t.accent)}


def _draw_blocks(c, blocks: list, first: int, top: int) -> tuple:
    """Wrap and draw ``blocks`` from line ``first``; returns (lines in total, lines shown)."""
    styles = _styles(c.theme)
    lines = []
    for text, style in blocks:
        size, weight, colour = styles.get(style, styles["body"])
        if text == "":
            lines.append(("", 8, weight, colour))
            continue
        for line in _wrap(c, text, size, weight):
            lines.append((line, size, weight, colour))
    def fitting(bottom: int) -> int:
        y, count = top, 0
        for line, size, _weight, _colour in lines[first:]:
            height = size + 5 if line else 8
            if y + height > bottom:
                break
            y += height
            count += 1
        return count

    shown = fitting(CONTENT_BOTTOM)
    more = first + shown < len(lines)
    if more:
        shown = max(1, fitting(CONTENT_BOTTOM - 16))     # leave room for "more"
    y = top
    for line, size, weight, colour in lines[first:first + shown]:
        if line:
            c.text(MARGIN, y, line, size, weight, colour)
        y += size + 5 if line else 8
    if more:
        c.text(SCREEN_W - MARGIN, CONTENT_BOTTOM - 14, "more ↓", 11, "semibold", c.theme.accent,
               anchor="ra")
    return len(lines), shown


def reader(title: str, blocks: list, first: int, hints: list, status=None,
           note: str = "") -> tuple:
    """Scrollable text. ``blocks``: [(text, style)] with style title|label|body|muted|good|warn|accent.

    Returns (image, lines in total, lines shown) so the caller can bound scrolling.
    """
    c = Canvas()
    status_bar(c, title, status)
    total, shown = _draw_blocks(c, blocks, first, CONTENT_TOP + 4)
    footer(c, [(g, label) for g, label, *_ in hints])
    if note:
        toast(c, note)
    return c.image, total, shown


def summary_blocks(summary) -> list:
    blocks = []
    if summary.went_well:
        blocks.append(("Went well", "label"))
        blocks += [(f"• {item}", "body") for item in summary.went_well]
        blocks.append(("", "body"))
    if summary.main_improvement:
        blocks.append(("Main improvement", "label"))
        blocks.append((summary.main_improvement, "good"))
        blocks.append(("", "body"))
    if summary.new_phrase:
        blocks.append(("New expression", "label"))
        blocks.append((summary.new_phrase, "accent"))
        if summary.new_phrase_meaning:
            blocks.append((summary.new_phrase_meaning, "muted"))
        blocks.append(("", "body"))
    if summary.next_exercise:
        blocks.append(("Next time", "label"))
        blocks.append((summary.next_exercise, "body"))
    return blocks or [("Lesson saved.", "body")]


def turn_blocks(turn, transcript: str = "") -> list:
    """Everything the teacher noted about the last turn, for the notes reader."""
    if turn is None:
        return [("Nothing yet.", "muted")]
    blocks = []
    if transcript:
        blocks += [("I heard", "label"), (f"“{transcript}”", "muted"), ("", "body")]
    if turn.recognition_doubt:
        blocks += [("I may have misheard you; the teacher asked to check.", "warn"), ("", "body")]
    if turn.intended_meaning:
        blocks += [("You meant", "label"), (turn.intended_meaning, "body"), ("", "body")]
    for correction in turn.corrections:
        if correction.you_said:
            blocks += [("You said", "label"), (correction.you_said, "muted")]
        blocks += [("Better", "label"), (correction.better, "good")]
        if correction.explanation:
            blocks.append((correction.explanation, "muted"))
        blocks.append(("", "body"))
    if turn.natural_alternative:
        blocks += [("More natural", "label"), (turn.natural_alternative, "accent"), ("", "body")]
    for note in turn.vocabulary_notes:
        blocks += [("New phrase", "label"), (note.phrase, "accent")]
        if note.meaning:
            blocks.append((note.meaning, "muted"))
        if note.example:
            blocks.append((f"e.g. {note.example}", "muted"))
        blocks.append(("", "body"))
    feedback = turn.interview_feedback or {}
    names = (("answered_question", "Answered the question?"), ("structure", "Structure"),
             ("language", "Language"), ("delivery", "Delivery"), ("improved_answer", "A stronger answer"))
    for key, label in names:
        if feedback.get(key):
            blocks += [(label, "label"), (feedback[key], "body"), ("", "body")]
    blocks += [("Teacher said", "label"), (turn.reply_text, "muted")]
    if turn.uncertainty:
        blocks += [("", "body"), ("Not sure: " + turn.uncertainty, "warn")]
    return blocks


# ---------------------------------------------------------------- others
def problem(page: str, title: str, remedy: str, choices: list, selected: int, hints: list,
            status=None, armed: bool = False) -> Image.Image:
    c = Canvas()
    status_bar(c, page, status)
    split = CONTENT_TOP + 104
    message(c, title, remedy, "warning", top=CONTENT_TOP, bottom=split)
    draw_list(c, [Row(label, kind="back" if label == "Back" else "action") for label in choices],
              selected, top=split + 4, bottom=CONTENT_BOTTOM)
    footer(c, _hints(hints, armed, "to choose"))
    return c.image


def progress(stats: dict, first: int, hints: list, status=None) -> tuple:
    c = Canvas()
    t = c.theme
    status_bar(c, "Progress", status)
    y = CONTENT_TOP + 4
    today, goal = stats["today"], stats["goal"]
    done = today >= goal
    c.text(MARGIN, y, "Today", 11, "semibold", t.text_faint)
    c.text(SCREEN_W - MARGIN, y, f"{today:.0f} of {goal} min", 12, "semibold",
           t.success if done else t.text_muted, anchor="ra")
    y += 18
    c.progress((MARGIN, y, SCREEN_W - MARGIN, y + 8), today / goal if goal else 0,
               fg=t.success if done else t.accent)
    total, shown = _draw_blocks(c, stats["blocks"], first, y + 20)
    footer(c, [(g, label) for g, label, *_ in hints])
    return c.image, total, shown


def phrase_detail(entry: dict, rows: list, selected: int, hints: list, status=None,
                  armed: bool = False, note: str = "") -> Image.Image:
    c = Canvas()
    t = c.theme
    status_bar(c, "Phrase", status)
    y = CONTENT_TOP + 4
    for line in _wrap(c, entry.get("phrase", ""), 18, "bold", TEXT_W, 2):
        c.text(MARGIN, y, line, 18, "bold", t.text)
        y += 23
    for line in _wrap(c, entry.get("meaning", ""), 13, "regular", TEXT_W, 2):
        c.text(MARGIN, y, line, 13, "regular", t.text_muted)
        y += 17
    if entry.get("example"):
        for line in _wrap(c, "e.g. " + entry["example"], 12, "regular", TEXT_W, 2):
            c.text(MARGIN, y, line, 12, "regular", t.text_faint)
            y += 16
    draw_list(c, rows, selected, top=max(y + 6, CONTENT_TOP + 96))
    footer(c, _hints(hints, armed, "to choose"))
    if note:
        toast(c, note)
    return c.image


def starting(status=None) -> Image.Image:
    c = Canvas()
    status_bar(c, "mLearning", status)
    message(c, "mLearning", "Getting your teacher ready…")
    return c.image


def meter_levels_demo(seconds: float = 2.0) -> list:
    """Levels that look like speech, for previews."""
    return [-62 + 30 * abs(math.sin(i * 0.7)) * (0.6 + 0.4 * math.sin(i * 0.23)) for i in range(17)]
