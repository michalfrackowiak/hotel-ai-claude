"""Template-context helpers for the guest portal."""


def _portal_context(request):
    lang = "en"
    section = request.session.get("_portal_section", "")
    if request.user.is_authenticated:
        try:
            lang = request.GET.get("lang") or request.user.profile.language or "en"
        except Exception:
            lang = "en"
    return {"lang": lang, "section": section}
