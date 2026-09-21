"""Writes the API documentation to disk so it can be read without the server:

    python manage.py build_api_docs                 # -> docs/api/

    docs/api/openapi.yaml    the OpenAPI 3 description (import into Postman, generate clients)
    docs/api/openapi.json    the same, as JSON
    docs/api/index.html      ONE self-contained page - open it in a browser, e-mail it, or host it
                             on any static host (S3, GitHub Pages, Netlify); needs no server and,
                             with the default --inline, no internet.

Regenerate after changing an endpoint, a serializer, a guide or the error catalogue
(core/test_openapi.py fails while the committed files are stale).
"""

import json
import urllib.request
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.template.loader import render_to_string
from drf_spectacular.generators import SchemaGenerator
from drf_spectacular.renderers import OpenApiJsonRenderer, OpenApiYamlRenderer

REDOC_CDN = "https://cdn.jsdelivr.net/npm/redoc@2.1.5/bundles/redoc.standalone.js"


def build_schema(server_url=None):
    """The finished OpenAPI document (guides, menu, examples, code samples).

    Servers are the relative `/api/v1` unless `server_url` is given - the export
    must not depend on whichever API_PUBLIC_URL happens to be in the environment."""
    schema = SchemaGenerator().get_schema(request=None, public=True)
    schema["servers"] = [{"url": server_url or "/api/v1", "description": "Server"}]
    return schema


class Command(BaseCommand):
    help = "Write the API docs (openapi.yaml, openapi.json and a self-contained index.html) to docs/api/."

    def add_arguments(self, parser):
        parser.add_argument("--out", default=str(Path(settings.BASE_DIR) / "docs" / "api"), help="Output directory.")
        parser.add_argument(
            "--server-url",
            default=None,
            help="Base URL the docs' 'Try it' and code samples should target, e.g. https://api.example.com/api/v1 (default: relative /api/v1).",
        )
        parser.add_argument(
            "--cdn",
            action="store_true",
            help="Load ReDoc from the jsDelivr CDN instead of embedding it (smaller file, but the page then needs internet).",
        )
        parser.add_argument("--redoc-js", default=None, help="Path to a local redoc.standalone.js to embed instead of downloading it.")

    def handle(self, *args, out, server_url, cdn, redoc_js, **options):
        out_dir = Path(out)
        out_dir.mkdir(parents=True, exist_ok=True)
        schema = build_schema(server_url)

        (out_dir / "openapi.yaml").write_bytes(OpenApiYamlRenderer().render(schema, renderer_context={}))
        (out_dir / "openapi.json").write_bytes(OpenApiJsonRenderer().render(schema, renderer_context={}))

        script = None
        if not cdn:
            script = self._redoc_js(redoc_js)
        html = render_to_string(
            "core/redoc_static.html",
            {"spec": schema, "redoc_js": script, "redoc_cdn": REDOC_CDN, "title": schema["info"]["title"]},
        )
        (out_dir / "index.html").write_text(html, encoding="utf-8")

        for name in ("index.html", "openapi.yaml", "openapi.json"):
            size = (out_dir / name).stat().st_size / 1024
            self.stdout.write(self.style.SUCCESS(f"wrote {out_dir / name}  ({size:,.0f} KB)"))

    def _redoc_js(self, path):
        if path:
            text = Path(path).read_text(encoding="utf-8")
        else:
            try:
                with urllib.request.urlopen(REDOC_CDN, timeout=30) as response:
                    text = response.read().decode("utf-8")
            except OSError as error:
                raise CommandError(
                    f"Couldn't download ReDoc ({error}). Pass --redoc-js <file> to embed a local copy, or --cdn to link to the CDN instead."
                )
        # An inline <script> ends at the first "</script"; keep the bundle from closing it early.
        return text.replace("</script", "<\\/script")
