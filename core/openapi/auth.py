"""Tells drf-spectacular how this project authenticates, so the generated
schema has proper `securitySchemes` instead of an "unresolved authenticator"
warning. There are three bearer-token principals (API client, admin, driver);
each operation says which it accepts with `auth=` in core.openapi.dsl. The
schemes themselves are declared in settings (SPECTACULAR_SETTINGS
APPEND_COMPONENTS)."""

from drf_spectacular.extensions import OpenApiAuthenticationExtension


class MultiPrincipalJWTScheme(OpenApiAuthenticationExtension):
    target_class = "accounts.authentication.JWTMultiPrincipalAuthentication"
    name = "BearerToken"

    def get_security_definition(self, auto_schema):
        return {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
            "description": "A JWT sent as `Authorization: Bearer <token>`. Which kind you need depends on the endpoint - see the Authentication guide.",
        }
