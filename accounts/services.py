from core.exceptions import DomainError

from .models import AdminUser


def create_admin_user(creator, email, password, first_name="", last_name=""):
    """Fix 8 — sub-user management. Always scoped to the CREATOR's own
    company — company_id is never accepted from the request body, so a
    client can't create an admin in a different company.
    """
    return AdminUser.objects.create_user(
        email=email,
        company=creator.company,
        password=password,
        first_name=first_name,
        last_name=last_name,
    )


def _get_admin_user(user_id, company_id):
    try:
        return AdminUser.objects.get(pk=user_id, company_id=company_id)
    except AdminUser.DoesNotExist:
        raise DomainError("ADMIN_USER_NOT_FOUND", "Admin user not found.", status_code=404)


def disable_admin_user(user_id, actor):
    admin_user = _get_admin_user(user_id, actor.company_id)

    if admin_user.id == actor.id:
        raise DomainError(
            "CANNOT_DISABLE_SELF", "You cannot disable your own account.", status_code=409
        )

    admin_user.is_active = False
    admin_user.save(update_fields=["is_active"])
    admin_user.soft_delete()
    return admin_user
