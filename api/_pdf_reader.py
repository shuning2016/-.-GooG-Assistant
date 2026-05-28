"""
_pdf_reader.py — PDF pre-read Q&A generation using Claude with Ian Ho's questioning style.

This module is imported by _briefing.py. It:
1. Loads the Ian QAer knowledge files (system_prompt.md, domain_knowledge.md,
   meeting_memory.md, persona_summary.md) from the project's pdf_reader/ directory.
2. Receives PDF bytes (downloaded from Gmail attachments).
3. Passes the PDF to Claude as a native document block (no pdfplumber needed on Vercel).
4. Parses Claude's output into structured question items for storage in Redis.

Redis key: pdf-qa:{YYYY-MM-DD}
"""

import base64
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

SGT = ZoneInfo("Asia/Singapore")

# ─── Knowledge file loader ────────────────────────────────────────────────────

# The pdf_reader/ directory sits next to the api/ directory at the project root.
_PDF_READER_DIR = Path(__file__).parent.parent / "pdf_reader"

_KNOWLEDGE_FILES = {
    "system_prompt": "system_prompt.md",
    "persona": "persona_summary.md",
    "domain": "domain_knowledge.md",
    "memory": "meeting_memory.md",
    "examples": "worked_examples.md",
}


def _load_knowledge() -> str:
    """Build the combined system prompt from Ian QAer knowledge files."""
    parts = []
    for key in ["system_prompt", "persona", "domain", "memory", "examples"]:
        fname = _KNOWLEDGE_FILES[key]
        fpath = _PDF_READER_DIR / fname
        if fpath.exists():
            content = fpath.read_text(encoding="utf-8").strip()
            if key == "system_prompt":
                # Use the system prompt as-is (it's already a system prompt)
                parts.append(content)
            else:
                # Append other knowledge files as supplementary context
                label = {
                    "persona": "PERSONA PROFILE",
                    "domain": "DOMAIN KNOWLEDGE",
                    "memory": "MEETING MEMORY",
                    "examples": "WORKED EXAMPLES",
                }.get(key, key.upper())
                parts.append(f"\n\n---\n## {label}\n\n{content}")
    return "\n".join(parts)


# ─── PDF selector (choose best PDF from a list of attachments) ────────────────

# Keywords in filenames that suggest "for update / informational" → lower priority
_UPDATE_KEYWORDS = frozenset([
    "for update", " update", "_update", "status", "tracker", "tracking",
    "report", "recap", "minutes", "fyi", "appendix",
])

# Keywords that suggest "purpose of discussion / main deck" → higher priority
_DISCUSSION_KEYWORDS = frozenset([
    "walkthrough", "discussion", "agenda", "proposal", "review",
    "deck", "monthly", "weekly", "bi-weekly", "biweekly", "catch-up",
    "catchup", "pre-read", "preread",
])


def select_best_pdf(pdf_attachments: list[dict]) -> dict | None:
    """
    Given a list of {filename, attachment_id, size} dicts, select the PDF that
    is most likely the main discussion deck (not a "for update" tracker).

    Priority:
      1. Filename contains a discussion keyword → best candidate
      2. Filename does NOT contain an update keyword
      3. Largest file size as tiebreaker
      4. If only one PDF, return it regardless of name

    Returns None if the list is empty.
    """
    if not pdf_attachments:
        return None
    if len(pdf_attachments) == 1:
        return pdf_attachments[0]

    def _score(att: dict) -> tuple[int, int]:
        name = att.get("filename", "").lower()
        discussion_score = any(kw in name for kw in _DISCUSSION_KEYWORDS)
        update_score = any(kw in name for kw in _UPDATE_KEYWORDS)
        # Higher is better: +2 for discussion keyword, -2 for update keyword
        score = (2 if discussion_score else 0) - (2 if update_score else 0)
        size = att.get("size", 0)
        return (score, size)

    return max(pdf_attachments, key=_score)


# ─── Question parser ───────────────────────────────────────────────────────────

def _parse_answers(raw_text: str) -> dict[str, str]:
    """
    Parse the ```answers code block into {question_text_lower: answer_text}.

    Expected block format:
        ```answers
        Slide N
        Q: question text
        A: answer text

        Others
        Q: question text
        A: answer text
        ```
    """
    answers: dict[str, str] = {}
    ans_match = re.search(r"```answers\s*\n(.*?)```", raw_text, re.DOTALL)
    if not ans_match:
        return answers

    current_q: str | None = None
    for line in ans_match.group(1).split("\n"):
        stripped = line.strip()
        if re.match(r"^Q:", stripped, re.IGNORECASE):
            current_q = stripped[2:].strip()
        elif re.match(r"^A:", stripped, re.IGNORECASE) and current_q is not None:
            answers[current_q.lower()] = stripped[2:].strip()
            current_q = None
    return answers


def _parse_questions(raw_text: str, pdf_name: str, today_str: str) -> list[dict]:
    """
    Parse Claude's Ian Ho–style output into structured question items.

    Expected format (inside a fenced block after '### Predicted Questions from Ian Ho'):

        Thanks.

        Slide N
        - question 1
        - question 2

        Slide M
        - question 3

        Others
        - question 4
        - question 5

    Proposed answers are parsed from a separate ```answers block and attached
    to each item as an "answer" field.

    Each question becomes an item:
        {
            "id": "...",
            "pdf_name": "...",
            "question": "...",
            "slide_ref": "Slide N" or "Others",
            "answer": "...",          # may be "" if not generated
            "type": "generated",
            "date": "YYYY-MM-DD",
            "created_at": "..."
        }
    """
    items = []

    # Extract just the Ian-format block from inside the first plain ``` code fence
    # (avoid matching the ```answers block)
    code_fence_match = re.search(r"```(?!answers)\s*\n(.*?)```", raw_text, re.DOTALL)
    if code_fence_match:
        ian_block = code_fence_match.group(1)
    else:
        # Fall back: look for everything after "Predicted Questions from Ian Ho"
        marker_match = re.search(
            r"###\s*Predicted Questions from Ian Ho\s*\n+(.*?)(?:###|\Z)",
            raw_text, re.DOTALL | re.IGNORECASE,
        )
        ian_block = marker_match.group(1) if marker_match else raw_text

    # Also extract the Deck Summary for context
    deck_summary = ""
    summary_match = re.search(
        r"###\s*Deck Summary\s*\n+(.*?)(?:###|\Z)",
        raw_text, re.DOTALL | re.IGNORECASE,
    )
    if summary_match:
        deck_summary = summary_match.group(1).strip()

    # Parse slide sections — sections start with "Slide N" or "Others"
    # Handles multi-line questions: continuation lines (not starting with "-" or
    # a slide header) are appended to the previous question.
    current_slide = "General"
    pending: dict | None = None  # question being accumulated

    def _flush(pending_item: dict | None) -> None:
        if pending_item and len(pending_item["question"]) > 3:
            pending_item["question"] = pending_item["question"].strip()
            items.append(pending_item)

    for line in ian_block.split("\n"):
        line_stripped = line.strip()
        if not line_stripped or line_stripped.lower() in ("thanks.", "thanks"):
            continue

        # Section header: "Slide N" or "Others"
        slide_header = re.match(r"^(Slide\s+\d+|Others)$", line_stripped, re.IGNORECASE)
        if slide_header:
            _flush(pending)
            pending = None
            current_slide = slide_header.group(1).strip()
            continue

        # Question bullet — start a new question
        if line_stripped.startswith("-") and len(line_stripped) > 2:
            _flush(pending)
            question_text = line_stripped.lstrip("- ").strip()
            if question_text and len(question_text) > 3:
                item_id = f"qa-{pdf_name[:20].replace(' ', '-').lower()}-{int(time.time() * 1000)}-{len(items)}"
                pending = {
                    "id": item_id,
                    "pdf_name": pdf_name,
                    "question": question_text,
                    "slide_ref": current_slide,
                    "answer": "",
                    "type": "generated",
                    "date": today_str,
                    "created_at": datetime.now(SGT).isoformat(),
                }
            else:
                pending = None
            continue

        # Continuation line — append to the current pending question
        if pending is not None:
            pending["question"] += " " + line_stripped

    _flush(pending)  # save the last question

    # If parsing failed to find structured output, extract all bullet points
    if not items:
        for line in raw_text.split("\n"):
            line_stripped = line.strip()
            if line_stripped.startswith("-") and len(line_stripped) > 5:
                question_text = line_stripped.lstrip("- ").strip()
                if question_text and "?" in question_text or len(question_text) > 15:
                    item_id = f"qa-fallback-{int(time.time() * 1000)}-{len(items)}"
                    items.append({
                        "id": item_id,
                        "pdf_name": pdf_name,
                        "question": question_text,
                        "slide_ref": "General",
                        "answer": "",
                        "type": "generated",
                        "date": today_str,
                        "created_at": datetime.now(SGT).isoformat(),
                    })

    # Attach proposed answers (matched by lowercased question text)
    answer_map = _parse_answers(raw_text)
    for item in items:
        q_lower = item["question"].lower()
        item["answer"] = answer_map.get(q_lower, "")

    # Add deck summary as the first item if we parsed any questions
    if items and deck_summary:
        summary_item = {
            "id": f"qa-summary-{int(time.time() * 1000)}",
            "pdf_name": pdf_name,
            "question": f"[DECK SUMMARY] {deck_summary}",
            "slide_ref": "Summary",
            "answer": "",
            "type": "generated",
            "date": today_str,
            "created_at": datetime.now(SGT).isoformat(),
        }
        items.insert(0, summary_item)

    return items


# ─── Main generation function ─────────────────────────────────────────────────

def generate_pdf_qa(
    pdf_bytes: bytes,
    pdf_name: str,
    today_str: str,
    client,  # anthropic.Anthropic instance
) -> list[dict]:
    """
    Generate predicted Ian Ho questions for a pre-read PDF deck.

    Uses Claude's native PDF document API (no pdfplumber required).
    Returns a list of structured question items for Redis storage.
    """
    system_prompt_text = _load_knowledge()

    pdf_b64 = base64.b64encode(pdf_bytes).decode("utf-8")

    user_content = [
        {
            "type": "text",
            "text": (
                f"Please analyze this pre-read deck: {pdf_name}\n\n"
                "Generate predicted questions from Ian Ho in his exact email format as specified. "
                "Follow ALL instructions in the system prompt: "
                "STEP 1 (data extraction), STEP 2 (slide reading pattern), "
                "cross-slide analysis, question cascading, and the Others section.\n\n"
                "Output in this exact order:\n"
                "1. ### Deck Summary — key takeaways, red flags, missing items\n"
                "2. ### Predicted Questions from Ian Ho — inside a ``` code block\n"
                "3. ### Confidence Notes\n"
                "4. ### Proposed Answers — concise 1–2 sentence data-driven answers for EVERY "
                "question above, using strictly the deck content. Use a ```answers code block:\n\n"
                "```answers\n"
                "Slide N\n"
                "Q: [exact question text]\n"
                "A: [answer from deck data]\n\n"
                "Others\n"
                "Q: [exact question text]\n"
                "A: [answer from deck data]\n"
                "```"
            ),
        },
        {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": pdf_b64,
            },
        },
    ]

    msg = client.messages.create(
        model="claude-3-5-haiku-20241022",
        max_tokens=4096,
        system=system_prompt_text,
        messages=[{"role": "user", "content": user_content}],
    )

    raw_text = msg.content[0].text
    return _parse_questions(raw_text, pdf_name, today_str)


# ─── Redis helpers ────────────────────────────────────────────────────────────

def save_pdf_qa(r, date: str, new_items: list[dict]) -> None:
    """Save PDF Q&A items to Redis, merging with any existing items for the date."""
    from upstash_redis import Redis

    key = f"pdf-qa:{date}"
    try:
        raw = r.get(key)
        existing: list[dict] = json.loads(raw) if isinstance(raw, str) else (raw or [])
        if not isinstance(existing, list):
            existing = []
    except Exception:
        existing = []

    # Add only genuinely new items (deduplicate by pdf_name + question text)
    existing_sigs = {
        (item.get("pdf_name", ""), item.get("question", ""))
        for item in existing
    }
    added = 0
    for item in new_items:
        sig = (item.get("pdf_name", ""), item.get("question", ""))
        if sig not in existing_sigs:
            existing.append(item)
            existing_sigs.add(sig)
            added += 1

    r.set(key, json.dumps(existing), ex=30 * 24 * 3600)
    return added
