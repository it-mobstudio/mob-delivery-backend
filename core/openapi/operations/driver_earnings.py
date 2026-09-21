"""The driver's wallet ledger and delivery stats."""

from core.openapi.dsl import DRIVER, doc, document, ex, ok, query_param
from drivers.serializers import DriverStatsSerializer, WalletSummarySerializer, WalletTransactionSerializer
from drivers.views import DriverStatsView, DriverWalletTransactionsView, DriverWalletView

TAG = "Driver earnings"
UTC_OFFSET = query_param(
    "utc_offset_minutes",
    "The driver's offset from UTC in **minutes east** (India: `330`), so \"today\" starts at the driver's own midnight. Optional; default `0` (UTC). Range -840 to 840.",
    type=int,
    default=0,
)

document(
    DriverWalletView,
    get=doc(
        id="driverGetWallet",
        tag=TAG,
        summary="My wallet",
        description="""
What the wallet screen shows: the current **balance** (what the company owes the driver), earnings and trip counts for today / this week / this month / lifetime,
total paid out so far, and the last seven days (oldest first, zero-filled) for a bar chart.

Every completed trip credits the driver a share of its fare (80% by default); the company records payouts, bonuses and penalties from its side.
""",
        auth=DRIVER,
        params=[UTC_OFFSET],
        responses={200: ok(WalletSummarySerializer, ex("wallet", "A driver with earnings"), ex("wallet.empty", "A new driver"))},
    ),
)

document(
    DriverWalletTransactionsView,
    get=doc(
        id="driverListWalletTransactions",
        tag=TAG,
        summary="My statement",
        description="""
The wallet ledger, **newest first**, 20 per page. Each row says what it was (`kind`), the signed `amount`, and the running `balance_after`, so the list reads like a bank statement.
""",
        auth=DRIVER,
        params=[
            query_param("kind", "Only these kinds, comma-separated: `trip_earning`, `bonus`, `penalty`, `payout`, `adjustment`. Example: `trip_earning,bonus`."),
            query_param("page", "Page number, starting at 1.", type=int),
            query_param("page_size", "Rows per page (default 20, maximum 100).", type=int),
        ],
        responses={200: ok(WalletTransactionSerializer, ex("wallet.transactions", "An entry in the statement", item=0))},
    ),
)

document(
    DriverStatsView,
    get=doc(
        id="driverGetStats",
        tag=TAG,
        summary="My delivery stats",
        description="""
Trip counts, fare totals, earnings, cash-on-delivery value and distance for **today** and **all time** - the numbers on the driver's dashboard.
""",
        auth=DRIVER,
        params=[UTC_OFFSET],
        responses={200: ok(DriverStatsSerializer, ex("stats", "A busy day"), ex("stats.empty", "Nothing yet"))},
    ),
)
