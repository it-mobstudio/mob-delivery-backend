from django.conf import settings


def maps(request):
    """The map tile source every web map uses (console and booking)."""
    return {"map_tiles": {"url": settings.MAP_TILE_URL, "attribution": settings.MAP_TILE_ATTRIBUTION}}
