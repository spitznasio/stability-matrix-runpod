import bleach

# Model/version descriptions are creator-authored rich HTML from CivitAI's
# API — rendered with `| safe` in the templates, so this allowlist is the
# only thing standing between an attacker-controlled description and script
# execution in this app's origin (which also renders Install/Download forms
# on the same page). Keep this list to formatting only: no <script>, no
# event-handler attributes, no iframe/object/embed.
ALLOWED_TAGS = [
    "p", "br", "hr",
    "strong", "b", "em", "i", "u", "s", "sub", "sup",
    "ul", "ol", "li",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "a", "img",
    "blockquote", "code", "pre",
    "span", "div",
]
ALLOWED_ATTRS = {
    "a": ["href", "title", "rel", "target"],
    "img": ["src", "alt", "title", "width", "height"],
}
ALLOWED_PROTOCOLS = ["http", "https", "mailto"]


def sanitize_html(html: str | None) -> str | None:
    if not html:
        return html
    return bleach.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRS,
        protocols=ALLOWED_PROTOCOLS,
        strip=True,
    )
