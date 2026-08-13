class CompanyScopedMixin:
    """Scopes a ViewSet to the authenticated principal's company.

    Both AdminUser and ApiClient (see accounts.authentication) expose
    `.company`, so this works the same regardless of which one authenticated
    the request — no thread-locals, no default-manager magic.
    """

    def get_queryset(self):
        qs = super().get_queryset()
        return qs.filter(company_id=self.request.user.company_id)

    def perform_create(self, serializer):
        serializer.save(company=self.request.user.company)
