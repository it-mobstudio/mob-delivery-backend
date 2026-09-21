from datetime import timedelta, timezone as dt_timezone
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from core.choices import WalletTransactionKind as Kind
from core.exceptions import DomainError

from .models import Driver, WalletTransaction

CENTS = Decimal("0.01")

# "Earnings" is what the driver made by working — trip payouts and bonuses.
# Penalties, payouts and corrections still move the balance and show up in the
# statement, but aren't income.
EARNING_KINDS = (Kind.TRIP_EARNING, Kind.BONUS)

# Which way a manual entry moves the balance. ADJUSTMENT is the only one whose
# caller chooses the sign, because a correction can go either way.
_MANUAL_SIGN = {Kind.BONUS: 1, Kind.PENALTY: -1, Kind.PAYOUT: -1}


def _money(value):
    return f"{Decimal(value or 0).quantize(CENTS, ROUND_HALF_UP):.2f}"


class WalletService:
    """A driver's wallet: an append-only ledger (drivers.models.WalletTransaction)
    of what trips paid them, bonuses, penalties and the payouts the company
    sent. The balance is just the sum of the ledger; every row also records the
    running total when it was written so a statement can be read top to bottom.
    """

    @staticmethod
    def balance(driver):
        return WalletTransaction.objects.filter(driver=driver).aggregate(total=Sum("amount"))["total"] or Decimal("0")

    @classmethod
    def add_entry(cls, driver, kind, amount, *, trip_id=None, description="", reference="", created_by=None):
        """Appends one ledger row. `amount` is signed exactly as stored."""
        amount = Decimal(amount).quantize(CENTS, ROUND_HALF_UP)
        if amount == 0:
            raise DomainError("INVALID_AMOUNT", "The amount can't be zero.", status_code=400)

        with transaction.atomic():
            # Serialise writers per driver, so two entries landing together
            # can't both read the same balance and write the same running total.
            Driver.objects.select_for_update().get(pk=driver.pk)
            balance = cls.balance(driver)
            new_balance = balance + amount
            if kind == Kind.PAYOUT and new_balance < 0:
                raise DomainError(
                    "INSUFFICIENT_BALANCE",
                    f"This payout is more than the driver's balance (₹{_money(balance)}).",
                    status_code=409,
                )
            return WalletTransaction.objects.create(
                company_id=driver.company_id,
                driver=driver,
                kind=kind,
                amount=amount,
                balance_after=new_balance,
                description=description[:255],
                reference=reference[:100],
                trip_id=trip_id,
                created_by=created_by,
            )

    @classmethod
    def record_manual(cls, driver, kind, amount, *, description="", reference="", created_by=None):
        """A company-recorded entry (a payout sent, a bonus, a fine, a
        correction). `amount` is what the admin typed: a positive number for
        every kind but ADJUSTMENT, whose sign is theirs."""
        if kind == Kind.TRIP_EARNING:
            raise DomainError("INVALID_KIND", "Trip earnings are credited automatically.", status_code=400)
        amount = Decimal(amount)
        sign = _MANUAL_SIGN.get(kind)
        if sign is not None:
            if amount <= 0:
                raise DomainError("INVALID_AMOUNT", "Enter a positive amount.", status_code=400)
            amount *= sign
        return cls.add_entry(
            driver, kind, amount, description=description, reference=reference, created_by=created_by
        )

    @classmethod
    def credit_trip_earning(cls, trip):
        """Pays the driver their share of a completed trip's fare. Safe to call
        twice for the same trip: it's paid once (the ledger's unique constraint
        is the backstop if two calls race)."""
        if trip.driver_id is None or trip.total_fare is None:
            return None
        existing = WalletTransaction.objects.filter(trip_id=trip.id, kind=Kind.TRIP_EARNING).first()
        if existing is not None:
            return existing

        earning = (trip.total_fare * settings.DRIVER_EARNING_PERCENT / 100).quantize(CENTS, ROUND_HALF_UP)
        trip.driver_earning = earning
        trip.save(update_fields=["driver_earning", "updated_at"])
        if earning <= 0:
            return None
        return cls.add_entry(
            trip.driver,
            Kind.TRIP_EARNING,
            earning,
            trip_id=trip.id,
            description=f"Delivery to {trip.drop_address}",
        )

    # -- reading -----------------------------------------------------------

    @staticmethod
    def transactions(driver, kind=None):
        qs = WalletTransaction.objects.filter(driver=driver)
        kinds = [k.strip() for k in (kind or "").split(",") if k.strip()]
        return qs.filter(kind__in=kinds) if kinds else qs

    @staticmethod
    def earnings_since(driver, since=None):
        """Total earned (trip payouts + bonuses) from `since` on, or ever."""
        qs = WalletTransaction.objects.filter(driver=driver, kind__in=EARNING_KINDS)
        if since is not None:
            qs = qs.filter(created_at__gte=since)
        return qs.aggregate(total=Sum("amount"))["total"] or Decimal("0")

    @classmethod
    def summary(cls, driver, utc_offset_minutes=0, now=None):
        """The wallet screen's numbers. Days are the *driver's* days (their UTC
        offset), not the server's: "today" starts at their local midnight and
        "this week" on their Monday."""
        tz = dt_timezone(timedelta(minutes=utc_offset_minutes))
        local_now = (now or timezone.now()).astimezone(tz)
        start_today = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        start_week = start_today - timedelta(days=start_today.weekday())
        start_month = start_today.replace(day=1)

        ledger = WalletTransaction.objects.filter(driver=driver)
        earned = ledger.filter(kind__in=EARNING_KINDS)

        def period(since):
            rows = earned if since is None else earned.filter(created_at__gte=since)
            agg = rows.aggregate(
                total=Sum("amount"), trips=Count("id", filter=Q(kind=Kind.TRIP_EARNING))
            )
            return {"earnings": _money(agg["total"]), "trips": agg["trips"] or 0}

        first_day = start_today - timedelta(days=6)
        per_day = {
            row["day"]: row
            for row in earned.filter(created_at__gte=first_day)
            .annotate(day=TruncDate("created_at", tzinfo=tz))
            .values("day")
            .annotate(total=Sum("amount"), trips=Count("id", filter=Q(kind=Kind.TRIP_EARNING)))
        }
        last_7_days = []
        for offset in range(7):
            day = (first_day + timedelta(days=offset)).date()
            row = per_day.get(day)
            last_7_days.append(
                {
                    "date": day.isoformat(),
                    "earnings": _money(row["total"] if row else 0),
                    "trips": row["trips"] if row else 0,
                }
            )

        lifetime = period(None)
        lifetime["payouts"] = _money(
            -(ledger.filter(kind=Kind.PAYOUT).aggregate(total=Sum("amount"))["total"] or 0)
        )
        return {
            "balance": _money(cls.balance(driver)),
            "currency": "INR",
            "today": period(start_today),
            "week": period(start_week),
            "month": period(start_month),
            "lifetime": lifetime,
            "last_7_days": last_7_days,
        }
