"""drf-spectacular postprocessing: the finishing touches that turn the generated
schema into the finished documentation.

* the menu (`tags` + ReDoc's `x-tagGroups`) and the guide pages
* a ready-to-run cURL / Python / JavaScript sample on every endpoint
  (`x-codeSamples`), built from the endpoint's own example so it is real
"""

import json
import re

from . import fields as field_docs
from . import tags as menu

HTTP_METHODS = ("get", "put", "post", "delete", "patch")
TOKEN_VARS = {"ApiClientToken": "ACCESS_TOKEN", "AdminToken": "ACCESS_TOKEN", "DriverToken": "DRIVER_TOKEN"}


def _base_name(component):
    """`PatchedVehicleRequest` -> `Vehicle` (the request/patch variants document the same fields)."""
    name = component
    if name.startswith("Patched"):
        name = name[len("Patched"):]
    if name.endswith("Request"):
        name = name[: -len("Request")]
    return name


def describe_fields(result, generator, request, public):
    """Fills in the description of every field that doesn't have one yet, from
    core/openapi/fields.py. (A description written on the serializer wins.)"""
    for component, schema in result.get("components", {}).get("schemas", {}).items():
        base = _base_name(component)
        for name, prop in (schema.get("properties") or {}).items():
            if prop.get("description"):
                continue
            text = field_docs.lookup(base, name)
            if not text:
                continue
            if set(prop) <= {"$ref", "readOnly"} and "$ref" in prop:
                # A bare $ref can't carry a description in OpenAPI 3.0; wrap it.
                ref = prop.pop("$ref")
                prop["allOf"] = [{"$ref": ref}]
            prop["description"] = text
    return result


BLANKABLE_ENUMS = {"cancelled_by"}  # read-only enums the API returns as "" when not applicable


def tidy_examples(result, generator, request, public):
    """Two honesty fixes the generator can't make on its own.

    * drf-spectacular fills a list endpoint's example with a made-up page
      (`count: 123`, a `next` link to api.example.org). Make it describe what is
      shown: the examples we give are single items, so `count` is the number of
      items in `results` and there is no next/previous page.
    * A read-only choice field that is empty when it doesn't apply (`cancelled_by`
      is `""` until a trip is cancelled) must allow the empty value in the schema.
    """
    components = result.get("components", {}).get("schemas", {})
    for item in result.get("paths", {}).values():
        for op in item.values():
            if not isinstance(op, dict):
                continue
            for response in op.get("responses", {}).values():
                media = response.get("content", {}).get("application/json", {})
                ref = (media.get("schema") or {}).get("$ref", "")
                if not ref.rsplit("/", 1)[-1].startswith("Paginated"):
                    continue
                for example in (media.get("examples") or {}).values():
                    value = example.get("value")
                    if isinstance(value, dict) and isinstance(value.get("results"), list):
                        value["count"] = len(value["results"])
                        value["next"] = None
                        value["previous"] = None

    for schema in list(components.values()):
        for name, prop in (schema.get("properties") or {}).items():
            refs = [x.get("$ref", "") for x in prop.get("allOf", [])]
            if name in BLANKABLE_ENUMS and refs and refs[0].endswith("Enum"):
                components.setdefault("BlankEnum", {"enum": [""], "type": "string", "description": "An empty value: not applicable."})
                description = prop.get("description")
                read_only = prop.get("readOnly")
                prop.clear()
                prop["oneOf"] = [{"$ref": refs[0]}, {"$ref": "#/components/schemas/BlankEnum"}]
                if description:
                    prop["description"] = description
                if read_only:
                    prop["readOnly"] = True
    return result


def finish_schema(result, generator, request, public):
    """Menu, guides and code samples."""
    result["tags"] = menu.build_tags()
    result["x-tagGroups"] = menu.build_groups()

    components = result.get("components", {}).get("schemas", {})
    for path, item in result.get("paths", {}).items():
        for method in HTTP_METHODS:
            operation = item.get(method)
            if operation:
                operation["x-codeSamples"] = _code_samples(path, method, operation, components)
    return result


# -- code samples -----------------------------------------------------------------------
def _resolve(schema, components):
    while isinstance(schema, dict) and "$ref" in schema:
        schema = components.get(schema["$ref"].rsplit("/", 1)[-1], {})
    return schema or {}


def _placeholder(schema, components, depth=0):
    """A plausible value for `schema` - used only when an endpoint has no example."""
    schema = _resolve(schema, components)
    if "allOf" in schema and schema["allOf"]:
        return _placeholder(schema["allOf"][0], components, depth)
    if schema.get("enum"):
        return schema["enum"][0]
    kind = schema.get("type")
    if kind == "object" or "properties" in schema:
        if depth > 3:
            return {}
        required = schema.get("required", [])
        props = schema.get("properties", {})
        return {k: _placeholder(v, components, depth + 1) for k, v in props.items() if k in required and not v.get("readOnly")}
    if kind == "array":
        return [_placeholder(schema.get("items", {}), components, depth + 1)]
    fmt = schema.get("format")
    if kind == "string":
        return {"uuid": "3fa85f64-5717-4562-b3fc-2c963f66afa6", "date": "2026-01-31", "email": "user@example.com", "uri": "https://example.com/file.pdf", "date-time": "2026-01-31T10:00:00Z"}.get(fmt, "string")
    if kind == "integer":
        return schema.get("minimum", 1) if isinstance(schema.get("minimum", 1), int) else 1
    if kind == "number":
        return 1.5
    if kind == "boolean":
        return True
    return None


def _first_example(media):
    examples = media.get("examples") or {}
    for example in examples.values():
        if "value" in example:
            return example["value"]
    return media.get("example")


def _var(name, previous):
    if name == "id":
        singular = re.sub(r"s$", "", previous.replace("-", "_")) if previous else "id"
        return f"{singular.upper()}_ID"
    return name.replace("_pk", "_id").upper()


def _url(path):
    segments = path.strip("/").split("/")
    out = []
    for i, seg in enumerate(segments):
        match = re.fullmatch(r"\{(\w+)\}", seg)
        out.append("${" + _var(match.group(1), segments[i - 1] if i else "") + "}" if match else seg)
    return "/" + "/".join(out)


def _code_samples(path, method, operation, components):
    url = _url(path)
    security = operation.get("security") or []
    token_var = None
    for requirement in security:
        for scheme in requirement:
            token_var = token_var or TOKEN_VARS.get(scheme)

    body = operation.get("requestBody", {}).get("content", {})
    json_body = body.get("application/json")
    form_body = body.get("multipart/form-data")

    payload = None
    form_fields = None
    if json_body:
        payload = _first_example(json_body)
        if payload is None:
            payload = _placeholder(json_body.get("schema", {}), components)
    elif form_body:
        schema = _resolve(form_body.get("schema", {}), components)
        required = schema.get("required", [])
        form_fields = []
        for name, prop in schema.get("properties", {}).items():
            if name in required:
                prop = _resolve(prop, components)
                is_file = prop.get("format") == "binary"
                form_fields.append((name, is_file, "" if is_file else _placeholder(prop, components)))

    query = [p for p in operation.get("parameters", []) if p.get("in") == "query" and p.get("required")]
    query_string = "?" + "&".join(f"{p['name']}=..." for p in query) if query else ""

    upper = method.upper()
    return [
        {"lang": "Shell", "label": "cURL", "source": _curl(upper, url + query_string, token_var, payload, form_fields)},
        {"lang": "Python", "label": "Python", "source": _python(method, url + query_string, token_var, payload, form_fields)},
        {"lang": "JavaScript", "label": "JavaScript", "source": _javascript(upper, url + query_string, token_var, payload, form_fields)},
    ]


def _fmt(value):
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _curl(method, url, token_var, payload, form_fields):
    lines = [f'curl -X {method} "$BASE_URL{url}"']
    if token_var:
        lines.append(f'  -H "Authorization: Bearer ${token_var}"')
    if payload is not None:
        lines.append('  -H "Content-Type: application/json"')
        lines.append("  -d '" + json.dumps(payload, indent=2, ensure_ascii=False).replace("\n", "\n  ") + "'")
    if form_fields:
        for name, is_file, value in form_fields:
            lines.append(f"  -F '{name}=@/path/to/photo.jpg'" if is_file else f"  -F '{name}={_fmt(value)}'")
    return " \\\n".join(lines)


def _py_url(url):
    return re.sub(r"\$\{(\w+)\}", r"{\1}", url)


def _python(method, url, token_var, payload, form_fields):
    lines = ["import requests", "", 'BASE_URL = "http://localhost:8000/api/v1"']
    if token_var:
        lines.append(f'{token_var} = "..."  # see the Authentication guide')
    lines.append("")
    args = [f'f"{{BASE_URL}}{_py_url(url)}"']
    if token_var:
        args.append(f'headers={{"Authorization": f"Bearer {{{token_var}}}"}}')
    if payload is not None:
        args.append("json=" + json.dumps(payload, indent=4, ensure_ascii=False).replace("\n", "\n    ").replace("true", "True").replace("false", "False").replace("null", "None"))
    if form_fields:
        files = [f'"{n}": open("photo.jpg", "rb")' for n, is_file, _ in form_fields if is_file]
        data = [f'"{n}": "{_fmt(v)}"' for n, is_file, v in form_fields if not is_file]
        if files:
            args.append("files={" + ", ".join(files) + "}")
        if data:
            args.append("data={" + ", ".join(data) + "}")
    lines.append(f"response = requests.{method}(\n    " + ",\n    ".join(args) + ",\n)")
    lines.append("print(response.status_code, response.json())")
    return "\n".join(lines)


def _javascript(method, url, token_var, payload, form_fields):
    lines = ['const BASE_URL = "http://localhost:8000/api/v1";']
    if token_var:
        lines.append(f'const {token_var} = "..."; // see the Authentication guide')
    lines.append("")
    options = [f'method: "{method}"']
    headers = []
    if token_var:
        headers.append(f'Authorization: `Bearer ${{{token_var}}}`')
    if payload is not None:
        headers.append('"Content-Type": "application/json"')
        options.append("body: JSON.stringify(" + json.dumps(payload, indent=2, ensure_ascii=False).replace("\n", "\n  ") + ")")
    if form_fields:
        lines.append("const form = new FormData();")
        for name, is_file, value in form_fields:
            lines.append(f'form.append("{name}", {"fileInput.files[0]" if is_file else json.dumps(_fmt(value))});')
        lines.append("")
        options.append("body: form")
    if headers:
        options.insert(1, "headers: { " + ", ".join(headers) + " }")
    lines.append(f"const response = await fetch(`${{BASE_URL}}{url}`, {{\n  " + ",\n  ".join(options) + ",\n});")
    lines.append("console.log(response.status, await response.json());")
    return "\n".join(lines)
