from django.db import migrations

KYC_FIELDS = [
    "aadhar_doc_url",
    "aadhar_status",
    "aadhar_verified_by",
    "aadhar_verified_at",
    "aadhar_rejection_note",
    "dl_doc_url",
    "dl_status",
    "dl_expiry_date",
    "dl_allowed_categories",
    "dl_verified_by",
    "dl_verified_at",
    "dl_rejection_note",
    "police_doc_url",
    "police_status",
    "police_verified_by",
    "police_verified_at",
    "police_rejection_note",
]


def copy_kyc_data_forward(apps, schema_editor):
    Driver = apps.get_model("drivers", "Driver")
    DriverKyc = apps.get_model("drivers", "DriverKyc")

    DriverKyc.objects.bulk_create(
        [DriverKyc(driver=driver, **{field: getattr(driver, field) for field in KYC_FIELDS}) for driver in Driver.objects.all()]
    )


def copy_kyc_data_backward(apps, schema_editor):
    Driver = apps.get_model("drivers", "Driver")
    DriverKyc = apps.get_model("drivers", "DriverKyc")

    for kyc in DriverKyc.objects.select_related("driver").all():
        driver = kyc.driver
        for field in KYC_FIELDS:
            setattr(driver, field, getattr(kyc, field))
        driver.save(update_fields=KYC_FIELDS)


class Migration(migrations.Migration):

    dependencies = [
        ('drivers', '0003_driverkyc'),
    ]

    operations = [
        migrations.RunPython(copy_kyc_data_forward, copy_kyc_data_backward),
    ]
