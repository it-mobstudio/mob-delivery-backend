from django.urls import reverse
from django.views.generic import TemplateView


class RedocView(TemplateView):
    """The reading view of the docs: menu on the left, examples on the right.
    (Swagger UI, at /api/docs/, is the one to *try calls* from.)"""

    template_name = "core/redoc.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["schema_url"] = reverse("schema") + "?format=json"
        context["swagger_url"] = reverse("swagger-ui")
        return context
