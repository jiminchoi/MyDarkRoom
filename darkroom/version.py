"""Single application version shared by the API and Windows bundle."""
VERSION = "0.13.2"


def editor_url(base):
    """Bypass previously cached, unversioned editor HTML on application upgrade."""
    return f"{base.rstrip('/')}/?v={VERSION}"
