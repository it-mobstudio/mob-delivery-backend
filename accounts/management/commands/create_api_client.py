import secrets

from django.core.management.base import BaseCommand, CommandError

from accounts.models import ApiClient, Company


class Command(BaseCommand):
    help = "Create an ApiClient for a company and print its client_id/client_secret once."

    def add_arguments(self, parser):
        parser.add_argument("--company-name", required=True)
        parser.add_argument("--name", required=True, help="Label for the API client, e.g. 'Partner X integration'.")

    def handle(self, *args, **options):
        try:
            company = Company.objects.get(name=options["company_name"])
        except Company.DoesNotExist:
            raise CommandError(f"Company '{options['company_name']}' does not exist.")

        raw_secret = secrets.token_urlsafe(32)
        webhook_signing_secret = secrets.token_hex(32)
        client = ApiClient(company=company, name=options["name"], webhook_signing_secret=webhook_signing_secret)
        client.set_secret(raw_secret)
        client.save()

        self.stdout.write(self.style.SUCCESS(f"Created API client '{client.name}' for '{company.name}'"))
        self.stdout.write(f"client_id: {client.client_id}")
        self.stdout.write(f"client_secret: {raw_secret}  (shown once — store it now)")
        self.stdout.write(
            f"webhook_signing_secret: {webhook_signing_secret}  (shown once — used to verify the "
            "X-Webhook-Signature header on delivered webhook events; give this to the client team)"
        )
        self.stdout.write("webhook_url is not set yet — update it via the admin panel once the client has an endpoint.")
