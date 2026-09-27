from django.contrib.auth import views as auth_views
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from ..access import SESSION_COMPANY, console_view, is_platform


class LoginView(auth_views.LoginView):
    template_name = "console/login.html"
    redirect_authenticated_user = True
    next_page = "console:dashboard"

    def form_valid(self, form):
        if not form.get_user().is_staff:
            form.add_error(None, "This login can't use the console. Ask your company's owner for access.")
            return self.form_invalid(form)
        return super().form_valid(form)


class LogoutView(auth_views.LogoutView):
    next_page = "console:login"


@console_view()
@require_POST
def switch_company(request):
    if is_platform(request.user):
        request.session[SESSION_COMPANY] = request.POST.get("company") or None
    target = request.POST.get("next") or "/admin/"
    if not url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
        target = "/admin/"
    return redirect(target)
