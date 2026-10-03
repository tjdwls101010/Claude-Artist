"""Which font Chrome really drew with, asked through the DevTools protocol.

`CSS.getPlatformFontsForNode` reports the faces that drew a node's own text, but under the face's legacy family name (name ID 1: "Paperlogy 7 Bold" for CSS "Paperlogy"), so comparing names would fail correct pages. Instead each text is drawn twice more with the family under test followed by one of two different installed Hangul faces: if the family is really available for that text, both copies draw with the same face; if it is missing or lacks some glyphs, the copies diverge. `document.fonts.check` is not used: it answers true for families that do not exist.
"""

from __future__ import annotations

SENTINELS = ("Apple SD Gothic Neo", "AppleMyungjo")
# Probes are appended after load; Chrome reports no fonts for text it has not laid out yet.
SETTLE = "() => document.fonts.ready.then(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))))"
GENERIC = {"serif", "sans-serif", "monospace", "cursive", "fantasy", "system-ui", "ui-serif", "ui-sans-serif", "ui-monospace", "ui-rounded", "emoji", "math", "fangsong"}

MARK_TEXT_ELEMENTS = """
() => {
  const out = [];
  let n = 0;
  for (const el of document.body ? document.body.querySelectorAll('*') : []) {
    if (['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE'].includes(el.tagName)) continue;
    const text = [...el.childNodes].filter(c => c.nodeType === 3).map(c => c.textContent).join('').trim();
    if (!text) continue;
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none' || el.getClientRects().length === 0) continue;
    const id = 'e' + (n++);
    el.setAttribute('data-artist-text', id);
    const label = el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + (el.className && typeof el.className === 'string' ? '.' + el.className.trim().split(/\\s+/).join('.') : '');
    out.push({id, label, text: text.slice(0, 40), family: cs.fontFamily, weight: cs.fontWeight, style: cs.fontStyle, stretch: cs.fontStretch});
  }
  return out;
}
"""

ADD_PROBES = """
([items, sentinels]) => {
  const box = document.createElement('div');
  box.style.cssText = 'position:absolute; left:-10000px; top:0; white-space:pre;';
  for (const it of items) {
    sentinels.forEach((s, i) => {
      const span = document.createElement('span');
      span.setAttribute('data-artist-probe', it.id + '-' + i);
      span.style.fontFamily = it.first + ', "' + s + '"';
      span.style.fontWeight = it.weight; span.style.fontStyle = it.style; span.style.fontStretch = it.stretch;
      span.textContent = it.text_full;
      box.appendChild(span);
    });
  }
  document.body.appendChild(box);
}
"""


def first_family(css: str) -> str:
    """The first entry of a computed font-family list, unquoted."""
    out, quote, buf = [], None, ""
    for ch in css:
        if quote:
            if ch == quote:
                quote = None
            else:
                buf += ch
        elif ch in "\"'":
            quote = ch
        elif ch == ",":
            out.append(buf.strip())
            buf = ""
        else:
            buf += ch
    out.append(buf.strip())
    return out[0] if out else ""


def _fonts(cdp, root: int, selector: str) -> list[dict]:
    node = cdp.send("DOM.querySelector", {"nodeId": root, "selector": selector})["nodeId"]
    if not node:
        return []
    return cdp.send("CSS.getPlatformFontsForNode", {"nodeId": node})["fonts"]


def font_problems(page) -> list[dict]:
    """Elements whose text was not drawn entirely with the first family of their font-family."""
    items = page.evaluate(MARK_TEXT_ELEMENTS)
    full = page.evaluate("() => Object.fromEntries([...document.querySelectorAll('[data-artist-text]')].map(el => [el.getAttribute('data-artist-text'), [...el.childNodes].filter(c => c.nodeType === 3).map(c => c.textContent).join('')]))")
    probed = []
    for it in items:
        it["first"] = first_family(it["family"])
        it["text_full"] = full.get(it["id"], it["text"])
        if it["first"].lower() not in GENERIC:
            probed.append({**it, "first": '"' + it["first"].replace('"', '\\"') + '"'})
    if probed:
        page.evaluate(ADD_PROBES, [probed, list(SENTINELS)])
        page.evaluate(SETTLE)
    cdp = page.context.new_cdp_session(page)
    cdp.send("DOM.enable")
    cdp.send("CSS.enable")
    root = cdp.send("DOM.getDocument", {"depth": -1})["root"]["nodeId"]
    probed_ids = {p["id"] for p in probed}
    problems = []
    for it in items:
        used = _fonts(cdp, root, f'[data-artist-text="{it["id"]}"]')
        faces = [f["postScriptName"] for f in used]
        names = sorted({f["familyName"] for f in used})
        reason = None
        if len(faces) > 1:
            reason = "some glyphs were drawn with another font"
        elif it["id"] in probed_ids:
            a = [f["postScriptName"] for f in _fonts(cdp, root, f'[data-artist-probe="{it["id"]}-0"]')]
            b = [f["postScriptName"] for f in _fonts(cdp, root, f'[data-artist-probe="{it["id"]}-1"]')]
            if a != b:
                reason = "the family is not installed or cannot draw this text"
            elif faces != a:
                reason = "the element was drawn with a different face than its family resolves to"
        if reason:
            problems.append({"element": it["label"], "text": it["text"], "intended": it["first"], "used": names, "reason": reason})
    cdp.detach()
    return problems


def family_available(page, names: list[str], sample: str) -> dict[str, bool]:
    """For each family name, whether CSS can name it and it draws `sample` on its own."""
    page.set_content("<!doctype html><meta charset='utf-8'><body></body>")
    items = [{"id": f"f{i}", "first": '"' + n.replace('"', '\\"') + '"', "weight": "400", "style": "normal", "stretch": "normal", "text_full": sample} for i, n in enumerate(names)]
    page.evaluate(ADD_PROBES, [items, list(SENTINELS)])
    page.evaluate(SETTLE)
    cdp = page.context.new_cdp_session(page)
    cdp.send("DOM.enable")
    cdp.send("CSS.enable")
    root = cdp.send("DOM.getDocument", {"depth": -1})["root"]["nodeId"]
    out = {}
    for it, name in zip(items, names):
        a = _fonts(cdp, root, f'[data-artist-probe="{it["id"]}-0"]')
        b = _fonts(cdp, root, f'[data-artist-probe="{it["id"]}-1"]')
        out[name] = bool(a) and [f["postScriptName"] for f in a] == [f["postScriptName"] for f in b] and len(a) == 1
    cdp.detach()
    return out
