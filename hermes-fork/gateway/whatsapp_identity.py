"""WhatsApp identity helpers (rebuilt for the portable build, 2026-08-27).

History: the original helpers were stripped in the portable build and this
module became a 2-function stub — but ``gateway.authz_mixin``,
``gateway.pairing`` and ``gateway.run`` still import
``expand_whatsapp_aliases`` from it. The missing symbol made every gateway
approval-notify attempt die with

    cannot import name 'expand_whatsapp_aliases' from 'gateway.whatsapp_identity'

which the approval layer surfaced as "BLOCKED: Failed to send approval
request" — i.e. under 默认权限 every terminal tool call failed outright and
the agent burned its turns failing (user-visible as "（未收到模型输出，任务
可能已被中断）").

Behavior mirrors tests/gateway/test_whatsapp_identity.py: expand a WhatsApp
id into its digit-only alias set, resolving LID→phone through the bridge's
lid-mapping files when present. Never raises.
"""

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def canonical_whatsapp_identifier(value: str) -> str:
    return value


def normalize_whatsapp_identifier(value: str) -> str:
    return value


def _digits(value) -> str:
    """Keep only [0-9] from a raw id / mapped value."""
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _read_lid_mapping(raw_id: str):
    """Read lid-mapping-<id>.json under either layout; None when absent.

    Modern layout : $HERMES_HOME/platforms/whatsapp/session/
    Legacy layout : $HERMES_HOME/whatsapp/session/

    Bridge writes the mapping under the LOCAL part of the JID (digits only,
    e.g. ``lid-mapping-999999999999999.json`` for ``999999999999999@lid``),
    so try both the full raw id and its ``@``-stripped local part. The file
    contains a JSON scalar (or {"jid": ...} object) holding the phone-side
    JID, e.g. "15551234567@s.whatsapp.net".
    """
    home = os.environ.get("HERMES_HOME")
    raw = str(raw_id or "").strip()
    if not home or not raw:
        return None
    local = raw.split("@", 1)[0] if "@" in raw else raw
    candidates = [raw, local] if raw != local else [raw]
    for rel in (
        os.path.join("platforms", "whatsapp", "session"),
        os.path.join("whatsapp", "session"),
    ):
        for key in candidates:
            safe_name = key.replace("/", "_").replace("\\", "_")
            path = Path(home) / rel / f"lid-mapping-{safe_name}.json"
            try:
                if not path.is_file():
                    continue
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    data = data.get("jid") or data.get("phone") or data.get("value")
                if isinstance(data, (int, float)):
                    return str(data)
                if isinstance(data, str):
                    return data
            except Exception as exc:
                logger.debug("lid-mapping read failed for %s: %s", path, exc)
    return None


def expand_whatsapp_aliases(value: str):
    """Expand a WhatsApp user id into all known alias forms.

    Returns a set of digit-only strings covering the input itself plus any
    phone number resolved from the bridge's LID→phone mapping files.
    """
    aliases = set()
    raw = str(value or "").strip()
    digits = _digits(raw.split("@", 1)[0]) if "@" in raw else _digits(raw)
    if digits:
        aliases.add(digits)
    mapped = _read_lid_mapping(raw)
    if mapped:
        mapped_digits = _digits(str(mapped).split("@", 1)[0] if "@" in str(mapped) else mapped)
        if mapped_digits:
            aliases.add(mapped_digits)
    return aliases
