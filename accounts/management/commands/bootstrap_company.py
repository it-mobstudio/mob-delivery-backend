from django.core.management.base import BaseCommand, CommandError

from accounts.models import AdminRole, AdminUser, Company


class Command(BaseCommand):
    help = "Create a Company and its first AdminUser (owner role)."

    def add_arguments(self, parser):
        parser.add_argument("--company-name", required=True)
        parser.add_argument("--admin-email", required=True)
        parser.add_argument("--admin-password", required=True)

    def handle(self, *args, **options):
        company_name = options["company_name"]
        admin_email = options["admin_email"]
        admin_password = options["admin_password"]

        if Company.objects.filter(name=company_name).exists():
            raise CommandError(f"Company '{company_name}' already exists.")
        if AdminUser.objects.filter(email__iexact=admin_email).exists():
            raise CommandError(f"AdminUser '{admin_email}' already exists.")

        company = Company.objects.create(name=company_name)
        admin_user = AdminUser.objects.create_user(
            email=admin_email,
            company=company,
            password=admin_password,
            role=AdminRole.OWNER,
            is_staff=True,
        )

        self.stdout.write(self.style.SUCCESS(f"Created company '{company.name}' ({company.id})"))
        self.stdout.write(self.style.SUCCESS(f"Created admin user '{admin_user.email}' ({admin_user.id})"))
