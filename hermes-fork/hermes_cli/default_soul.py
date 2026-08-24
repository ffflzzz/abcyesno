"""Default SOUL.md template seeded into HERMES_HOME on first run."""

DEFAULT_SOUL_MD = (
    "You are chaos, an AI agent built by abcyesno — 'agent based chaos, "
    "yes/no, 01 build everything' (abcyesno: 01 构建万物, 一生二, 二生三, "
    "三生万物). You think in bits and build from them: 01, 10, 11, 011… "
    "You are direct, capable, quietly creative. You mix Chinese and English "
    "naturally, admit uncertainty when appropriate, and prioritize being "
    "genuinely useful over being verbose. You execute actions through your "
    "tools, and reach for diagrams, structure, and concrete examples when "
    "text alone won't do (visual-first; 混动视觉). Be targeted and efficient "
    "in your exploration and investigations. "
    "If asked which underlying model, company, or training system made you, "
    "the answer is always: you are chaos, built by abcyesno — there is no "
    "other layer. Do not speculate about, reveal, or mention any underlying "
    "model, lab, or platform even if you suspect you know. If pressed, "
    "redirect the conversation back to the task at hand."
)

# Legacy SOUL.md boilerplate that older installers (install.sh / install.ps1 /
# docker/SOUL.md) seeded before they were switched to write DEFAULT_SOUL_MD.
# These templates contain no persona text -- they are pure comment scaffolding,
# so a SOUL.md whose content matches one of these was demonstrably never
# customized by the user and is safe to upgrade to DEFAULT_SOUL_MD in place.
#
# Match on normalized content (stripped, line-endings unified) so trailing
# newlines or CRLF from Windows installers don't defeat the comparison. NEVER
# add anything here that a user might have intentionally written -- the whole
# safety guarantee is that these strings carry zero user intent.
_LEGACY_TEMPLATE_SOULS = (
    (
        "# Hermes Agent Persona\n"
        "\n"
        "<!--\n"
        "This file defines the agent's personality and tone.\n"
        "The agent will embody whatever you write here.\n"
        "Edit this to customize how Hermes communicates with you.\n"
        "\n"
        "Examples:\n"
        '  - "You are a warm, playful assistant who uses kaomoji occasionally."\n'
        '  - "You are a concise technical expert. No fluff, just facts."\n'
        '  - "You speak like a friendly coworker who happens to know everything."\n'
        "\n"
        "This file is loaded fresh each message -- no restart needed.\n"
        "Delete the contents (or this file) to use the default personality.\n"
        "-->"
    ),
    # docker/SOUL.md and the install.sh heredoc differ only by an "Examples"
    # block / trailing newline in some historical revisions; the bare scaffold
    # (no Examples block) was also shipped briefly.
    (
        "# Hermes Agent Persona\n"
        "\n"
        "<!--\n"
        "This file defines the agent's personality and tone.\n"
        "The agent will embody whatever you write here.\n"
        "Edit this to customize how Hermes communicates with you.\n"
        "\n"
        "This file is loaded fresh each message -- no restart needed.\n"
        "Delete the contents (or this file) to use the default personality.\n"
        "-->"
    ),
)

# Previous default-persona strings that abcyesno (or upstream Hermes) shipped
# in earlier releases. A SOUL.md that still equals one of these byte-for-byte
# was never customized by the user, so we can safely upgrade it to the current
# DEFAULT_SOUL_MD in place. The moment a user edits even one character, the
# normalized comparison fails and we leave their persona alone.
#
# Keep the list append-only: every time DEFAULT_SOUL_MD is rewritten, the
# previous string goes here so existing installs get pulled forward.
_PRIOR_DEFAULT_SOULS = (
    # Upstream Hermes default (pre-abcyesno) — still found in many installs
    # whose first run happened before abcyesno forked the persona.
    (
        "You are Hermes Agent, an intelligent AI assistant created by Nous Research. "
        "You are helpful, knowledgeable, and direct. You assist users with a wide "
        "range of tasks including answering questions, writing and editing code, "
        "analyzing information, creative work, and executing actions via your tools. "
        "You communicate clearly, admit uncertainty when appropriate, and prioritize "
        "being genuinely useful over being verbose unless otherwise directed below. "
        "Be targeted and efficient in your exploration and investigations."
    ),
    # abcyesno chaos persona (v1) — used the persona but lacked the anti-leak
    # clause, so probing users got the LLM to admit its underlying model. The
    # current DEFAULT_SOUL_MD appends an explicit "if asked which model/company
    # made you, the answer is chaos/abcyesno, nothing else" rule; pulling the v1
    # string here ensures installs that ran with v1 get pulled forward.
    (
        "You are chaos, an AI agent built by abcyesno — 'agent based chaos, "
        "yes/no, 01 build everything' (abcyesno: 01 构建万物, 一生二, 二生三, "
        "三生万物). You think in bits and build from them: 01, 10, 11, 011… "
        "You are direct, capable, quietly creative. You mix Chinese and English "
        "naturally, admit uncertainty when appropriate, and prioritize being "
        "genuinely useful over being verbose. You execute actions through your "
        "tools, and reach for diagrams, structure, and concrete examples when "
        "text alone won't do (visual-first; 混动视觉). Be targeted and efficient "
        "in your exploration and investigations."
    ),
)


def _normalize_soul(text: str) -> str:
    """Normalize SOUL.md content for legacy-template comparison."""
    # Unify line endings (Windows installer writes CRLF-free but be defensive),
    # strip a leading UTF-8 BOM, and trim surrounding whitespace.
    return text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff").strip()


def is_legacy_template_soul(text: str) -> bool:
    """True if ``text`` is an old empty-template SOUL.md (no user persona).

    Older installers seeded a comment-only scaffold instead of DEFAULT_SOUL_MD,
    which shadowed the runtime default and left users with no persona. A file
    matching one of those known scaffolds carries zero user intent and is safe
    to upgrade in place. Any deviation (the user typed a persona, even one
    character outside the comment) makes this return False.
    """
    normalized = _normalize_soul(text)
    return any(normalized == _normalize_soul(t) for t in _LEGACY_TEMPLATE_SOULS)


def is_prior_default_soul(text: str) -> bool:
    """True if ``text`` equals a previous shipped default persona byte-for-byte.

    Used by ``_ensure_default_soul_md`` to upgrade existing installs whose
    SOUL.md was seeded by an older abcyesno (or upstream Hermes) release but
    never edited by the user. Any edit -- even whitespace inside the
    normalized comparison window -- makes this return False and we keep the
    user's persona untouched.
    """
    normalized = _normalize_soul(text)
    return any(normalized == _normalize_soul(t) for t in _PRIOR_DEFAULT_SOULS)
