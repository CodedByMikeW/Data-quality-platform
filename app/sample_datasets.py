"""
Ready-made business datasets for the "Try a sample" button: the kind of table a
company really has (orders, customers, invoices...), each with a handful of
everyday data problems mixed in, so a visitor with no CSV of their own can run a
check and see it find something.

Nothing is stored. A sample is generated when it is asked for, from a seed - the
same kind and seed always give the same file (dates are counted back from
today). Each problem is put on its own rows, and every column that is ever
flagged is flagged on purpose: the tests run the real checks over every kind
and fail if one flags a column that was meant to be clean.
"""
import csv
import io
import random
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Callable

from faker import Faker

SAMPLE_DATASET_PREFIX = "sample-"  # the dataset label a sample is checked under, e.g. "sample-orders"
PREVIEW_ROWS = 100  # rows the "View data" panel shows; the download has them all

_DOMAINS = ["gmail.com", "outlook.com", "yahoo.com", "icloud.com", "proton.me", "fastmail.com"]


# --------------------------------------------------------------------------
# small building blocks
# --------------------------------------------------------------------------


def _email(rng: random.Random, first: str, last: str, domains: list[str] = _DOMAINS) -> str:
    first, last = re.sub(r"[^a-z]", "", first.lower()), re.sub(r"[^a-z]", "", last.lower())
    suffix = str(rng.randint(2, 99)) if rng.random() < 0.35 else ""
    return f"{first}.{last}{suffix}@{rng.choice(domains)}"


def _bell(rng: random.Random, mean: float, spread: float, limit: float = 2.2) -> float:
    """A normally distributed number, never more than `limit` spreads from the mean.

    The cut-off keeps ordinary values inside the range the outlier check calls
    typical, so the only outliers are the ones put there on purpose. (Draws
    outside it are thrown away rather than clamped, which would pile many rows
    onto exactly the same value.)"""
    while True:
        z = rng.gauss(0, 1)
        if abs(z) <= limit:
            return mean + z * spread


def _weighted(rng: random.Random, options: dict[str, int]) -> str:
    return rng.choices(list(options), weights=list(options.values()))[0]


def _days_ago(rng: random.Random, today: date, most: int, least: int = 0) -> date:
    return today - timedelta(days=rng.randint(least, most))


class _Mess:
    """Hands out row positions so every problem lands on rows of its own."""

    def __init__(self, rng: random.Random, n_rows: int) -> None:
        self.rng = rng
        self._free = list(range(n_rows))
        rng.shuffle(self._free)

    def rows(self, low: int, high: int | None = None) -> list[int]:
        count = self.rng.randint(low, high) if high is not None else low
        taken, self._free = self._free[:count], self._free[count:]
        return taken


def _break_email(rng: random.Random, email: str) -> str:
    name, _, domain = email.partition("@")
    return rng.choice([f"{name}{domain}", f"{name}@", f"{name}@{domain.split('.')[0]}", f"{name} @{domain}", f"{name}@@{domain}"])


def _break_date(rng: random.Random, iso: str) -> str:
    y, m, d = iso[:4], iso[5:7], iso[8:10]
    return rng.choice([f"{m}/{d}/{y}", f"{d}-{m}-{y}", f"{y}/{m}/{d}", f"{int(d)}.{int(m)}.{y}"])


def _break_id(rng: random.Random, value: str) -> str:
    prefix, _, digits = value.partition("-")
    return rng.choice([f"{prefix}-{digits[-3:]}", digits, f"{prefix.lower()}-{digits}", f"{prefix}{digits}"])


def _typos(rng: random.Random, rows: list[dict], mess: _Mess, column: str, variants: list[str], each: tuple[int, int] = (2, 5)) -> None:
    """Puts each odd spelling in a few rows - few enough that it stands out from the column's usual values."""
    for variant in variants:
        for i in mess.rows(*each):
            rows[i][column] = variant


def _spike(rows: list[dict], positions: list[int], column: str, rng: random.Random) -> None:
    """Makes the chosen values several times bigger than the column's biggest - a slipped decimal point or an extra zero."""
    biggest = max(row[column] for row in rows if row[column] is not None)
    for i in positions:
        value = biggest * rng.uniform(2.5, 6)
        rows[i][column] = int(value) if isinstance(biggest, int) else round(value, 2)


def _double_entries(rows: list[dict], positions: list[int]) -> list[dict]:
    """Each chosen row appears twice in a row - the same record keyed in two times."""
    chosen = set(positions)
    result: list[dict] = []
    for i, row in enumerate(rows):
        result.append(row)
        if i in chosen:
            result.append(dict(row))
    return result


# --------------------------------------------------------------------------
# the datasets
# --------------------------------------------------------------------------
# Each builder makes clean rows, then mixes in problems - a few percent of the
# rows for each kind of problem, so a check finds something real but the table
# still looks like data a business would be using.


def _pct(n: int, low: float, high: float) -> tuple[int, int]:
    return max(1, round(n * low / 100)), max(2, round(n * high / 100))


def _orders(rng: random.Random, fake: Faker, n: int, today: date) -> list[dict]:
    rows = []
    for i in range(1, n + 1):
        items = rng.choices([1, 2, 3, 4], weights=[50, 28, 14, 8])[0]
        rows.append(
            {
                "order_id": f"ORD-{i:06d}",
                "customer_email": _email(rng, fake.first_name(), fake.last_name()),
                "order_date": _days_ago(rng, today, 360).isoformat(),
                "status": _weighted(rng, {"delivered": 62, "shipped": 14, "processing": 10, "cancelled": 8, "returned": 6}),
                "channel": _weighted(rng, {"web": 55, "mobile app": 30, "marketplace": 15}),
                "items": items,
                "order_total": round(_bell(rng, 100 + 5 * items, 18, limit=2.0), 2),
            }
        )

    mess = _Mess(rng, n)
    for i in mess.rows(*_pct(n, 2.5, 3.5)):
        rows[i]["customer_email"] = None
    for i in mess.rows(*_pct(n, 1.2, 2)):
        rows[i]["order_date"] = None
    for i in mess.rows(*_pct(n, 2.2, 3.2)):
        rows[i]["customer_email"] = _break_email(rng, rows[i]["customer_email"])
    for i in mess.rows(*_pct(n, 1.5, 2.5)):
        rows[i]["order_date"] = _break_date(rng, rows[i]["order_date"])
    for i in mess.rows(3, 6):
        rows[i]["order_id"] = _break_id(rng, rows[i]["order_id"])
    _spike(rows, mess.rows(5, 9), "order_total", rng)
    _typos(rng, rows, mess, "status", ["Delivered", "shiped", "canceled"])
    _typos(rng, rows, mess, "channel", ["Mobile App", "websit"])
    return _double_entries(rows, mess.rows(*_pct(n, 2, 3)))


def _customers(rng: random.Random, fake: Faker, n: int, today: date) -> list[dict]:
    rows = []
    for i in range(1, n + 1):
        first, last = fake.first_name(), fake.last_name()
        rows.append(
            {
                "customer_id": f"CUS-{i:05d}",
                "full_name": f"{first} {last}",
                "email": _email(rng, first, last),
                "phone": f"+1-{rng.randint(201, 989)}-{rng.randint(200, 989)}-{rng.randint(1000, 9999)}",
                "signup_date": _days_ago(rng, today, 1000).isoformat(),
                "plan": _weighted(rng, {"free": 45, "pro": 40, "business": 15}),
                "country": _weighted(rng, {"US": 48, "CA": 12, "GB": 12, "DE": 10, "AU": 9, "FR": 9}),
                "lifetime_value": round(_bell(rng, 700, 350, limit=2.0), 2),
            }
        )

    mess = _Mess(rng, n)
    for i in mess.rows(*_pct(n, 3, 4.5)):
        rows[i]["phone"] = None
    for i in mess.rows(*_pct(n, 1.5, 2.2)):
        rows[i]["email"] = None
    for i in mess.rows(*_pct(n, 2.2, 3.2)):
        rows[i]["email"] = _break_email(rng, rows[i]["email"])
    for i in mess.rows(*_pct(n, 1.8, 2.8)):
        rows[i]["phone"] = rows[i]["phone"].replace("+1-", "").replace("-", "") if rows[i]["phone"] else "5551234"
    for i in mess.rows(*_pct(n, 1.2, 2)):
        rows[i]["signup_date"] = _break_date(rng, rows[i]["signup_date"])
    for i in mess.rows(2, 5):
        rows[i]["customer_id"] = _break_id(rng, rows[i]["customer_id"])
    _spike(rows, mess.rows(5, 9), "lifetime_value", rng)
    _typos(rng, rows, mess, "plan", ["Bussiness", "pro "])
    _typos(rng, rows, mess, "country", ["USA", "U.S."])
    return _double_entries(rows, mess.rows(*_pct(n, 2, 3)))


def _employees(rng: random.Random, fake: Faker, n: int, today: date) -> list[dict]:
    departments = {"Engineering": 26, "Sales": 20, "Support": 16, "Operations": 12, "Finance": 8, "Marketing": 10, "People": 8}
    pay = {"Engineering": 100000, "Sales": 84000, "Support": 62000, "Operations": 70000, "Finance": 90000, "Marketing": 78000, "People": 72000}
    rows = []
    for i in range(1, n + 1):
        first, last = fake.first_name(), fake.last_name()
        department = _weighted(rng, departments)
        rows.append(
            {
                "employee_id": f"EMP-{i:04d}",
                "full_name": f"{first} {last}",
                "work_email": _email(rng, first, last, ["northfield.example"]),
                "department": department,
                "hire_date": _days_ago(rng, today, 3000, 20).isoformat(),
                "salary": int(round(_bell(rng, pay[department], pay[department] * 0.16, limit=1.8), -2)),
                "status": _weighted(rng, {"active": 78, "on leave": 12, "terminated": 10}),
            }
        )

    mess = _Mess(rng, n)
    for i in mess.rows(*_pct(n, 2.5, 3.5)):
        rows[i]["work_email"] = None
    for i in mess.rows(*_pct(n, 1.2, 2)):
        rows[i]["hire_date"] = None
    for i in mess.rows(*_pct(n, 2, 3)):
        rows[i]["work_email"] = _break_email(rng, rows[i]["work_email"])
    for i in mess.rows(*_pct(n, 1.2, 2)):
        rows[i]["hire_date"] = _break_date(rng, rows[i]["hire_date"])
    for i in mess.rows(2, 4):
        rows[i]["employee_id"] = _break_id(rng, rows[i]["employee_id"])
    _spike(rows, mess.rows(3, 5), "salary", rng)
    for i in mess.rows(2, 3):
        rows[i]["salary"] = rows[i]["salary"] // 100  # a missing "00"
    _typos(rng, rows, mess, "department", ["Enginering", "sales "])
    _typos(rng, rows, mess, "status", ["Actve"])
    return _double_entries(rows, mess.rows(*_pct(n, 1.8, 2.6)))


def _invoices(rng: random.Random, fake: Faker, n: int, today: date) -> list[dict]:
    vendors = [
        "Alder & Finch Supplies", "Kestrel Logistics", "Maple Ridge Packaging", "Orion Cloud Services", "Bluepeak Consulting",
        "Fernwood Catering", "Tidewater Print Co.", "Summit IT Hardware", "Lumen Utilities", "Cobalt Security",
        "Juniper Cleaning", "Redwood Legal Partners", "Quarry Office Furniture", "Harbor Telecom",
    ]
    rows = []
    for i in range(1, n + 1):
        issued = _days_ago(rng, today, 300, 1)
        terms = rng.choice([15, 30, 30, 45, 60])
        rows.append(
            {
                "invoice_id": f"INV-{i:06d}",
                "vendor": rng.choice(vendors),
                "invoice_date": issued.isoformat(),
                "due_date": (issued + timedelta(days=terms)).isoformat(),
                "amount": round(_bell(rng, 1500, 700, limit=2.0), 2),
                "currency": _weighted(rng, {"USD": 70, "EUR": 18, "GBP": 12}),
                "status": _weighted(rng, {"paid": 58, "open": 27, "overdue": 10, "disputed": 5}),
            }
        )

    mess = _Mess(rng, n)
    for i in mess.rows(*_pct(n, 2.2, 3.2)):
        rows[i]["vendor"] = None
    for i in mess.rows(*_pct(n, 2, 3)):
        rows[i]["due_date"] = None
    for i in mess.rows(*_pct(n, 2, 3)):
        rows[i]["invoice_date"] = _break_date(rng, rows[i]["invoice_date"])
    for i in mess.rows(*_pct(n, 1.2, 2)):
        rows[i]["due_date"] = _break_date(rng, rows[i]["due_date"])
    for i in mess.rows(3, 6):
        rows[i]["invoice_id"] = _break_id(rng, rows[i]["invoice_id"])
    _spike(rows, mess.rows(5, 9), "amount", rng)
    _typos(rng, rows, mess, "currency", ["usd", "US$"])
    _typos(rng, rows, mess, "status", ["Paid", "overdu"])
    return _double_entries(rows, mess.rows(*_pct(n, 2.5, 3.5)))  # the classic: an invoice keyed in twice


def _inventory(rng: random.Random, fake: Faker, n: int, today: date) -> list[dict]:
    catalog = {
        "Electronics": (22, ["Wireless", "Compact", "Portable", "Smart"], ["Speaker", "Charger", "Webcam", "Router", "Headset"]),
        "Home": (20, ["Ceramic", "Linen", "Oak", "Matte"], ["Lamp", "Cushion", "Shelf", "Vase", "Rug"]),
        "Garden": (16, ["Steel", "Cedar", "Folding", "Heavy-duty"], ["Planter", "Hose Reel", "Pruners", "Trellis", "Gloves"]),
        "Office": (16, ["Ergonomic", "Recycled", "Bamboo", "Slim"], ["Desk Pad", "Notebook", "Stand", "Organiser", "Chair Mat"]),
        "Sports": (14, ["Lightweight", "Padded", "Quick-dry", "Pro"], ["Yoga Mat", "Water Bottle", "Gym Bag", "Jump Rope", "Towel"]),
        "Toys": (12, ["Wooden", "Magnetic", "Plush", "Mini"], ["Blocks", "Puzzle", "Train Set", "Robot", "Kite"]),
    }
    warehouses = {"North": 30, "South": 26, "East": 24, "West": 20}
    rows = []
    for i in range(1, n + 1):
        category = _weighted(rng, {name: weight for name, (weight, _, _) in catalog.items()})
        _, adjectives, nouns = catalog[category]
        rows.append(
            {
                "product_id": f"PRD-{i:05d}",
                "product_name": f"{rng.choice(adjectives)} {rng.choice(nouns)}",
                "category": category,
                "warehouse": _weighted(rng, warehouses),
                "units_in_stock": int(_bell(rng, 130, 55)),  # 55, not 60: 130 - 2.2 x 60 would dip below zero
                "unit_price": round(_bell(rng, 105, 30), 2),
                "last_counted": _days_ago(rng, today, 120).isoformat(),
            }
        )

    mess = _Mess(rng, n)
    for i in mess.rows(*_pct(n, 2.5, 3.5)):
        rows[i]["product_name"] = None
    for i in mess.rows(*_pct(n, 2, 3)):
        rows[i]["last_counted"] = None
    for i in mess.rows(*_pct(n, 2, 3)):
        rows[i]["last_counted"] = _break_date(rng, rows[i]["last_counted"])
    for i in mess.rows(3, 6):
        rows[i]["product_id"] = _break_id(rng, rows[i]["product_id"])
    for i in mess.rows(4, 7):
        rows[i]["units_in_stock"] = max(rows[i]["units_in_stock"], 20) * rng.choice([50, 100])  # an extra zero or two
    _spike(rows, mess.rows(3, 5), "unit_price", rng)
    _typos(rng, rows, mess, "category", ["Electronic", "garden"])
    _typos(rng, rows, mess, "warehouse", ["Nroth", "south "])
    return _double_entries(rows, mess.rows(*_pct(n, 2, 3)))


def _tickets(rng: random.Random, fake: Faker, n: int, today: date) -> list[dict]:
    agents = ["Priya N.", "Marcus T.", "Elena V.", "Jonas K.", "Aisha B.", "Tom R.", "Lucia M.", "Ben O."]
    rows = []
    for i in range(1, n + 1):
        opened = datetime.combine(_days_ago(rng, today, 90), datetime.min.time()) + timedelta(
            hours=rng.randint(7, 20), minutes=rng.randint(0, 59), seconds=rng.randint(0, 59)
        )
        rows.append(
            {
                "ticket_id": f"TKT-{i:06d}",
                "opened_at": opened.strftime("%Y-%m-%d %H:%M:%S"),
                "customer_email": _email(rng, fake.first_name(), fake.last_name()),
                "channel": _weighted(rng, {"email": 40, "chat": 30, "phone": 20, "web form": 10}),
                "priority": _weighted(rng, {"low": 25, "medium": 45, "high": 22, "urgent": 8}),
                "status": _weighted(rng, {"resolved": 52, "closed": 30, "pending": 12, "open": 6}),
                "agent": rng.choice(agents),
                "resolution_minutes": int(_bell(rng, 900, 420, limit=2.0)),
            }
        )

    mess = _Mess(rng, n)
    for i in mess.rows(*_pct(n, 2.5, 3.5)):
        rows[i]["customer_email"] = None
    for i in mess.rows(*_pct(n, 1.5, 2.5)):
        rows[i]["agent"] = None
    for i in mess.rows(*_pct(n, 2, 3)):
        rows[i]["customer_email"] = _break_email(rng, rows[i]["customer_email"])
    for i in mess.rows(*_pct(n, 1.2, 2)):
        rows[i]["opened_at"] = _break_date(rng, rows[i]["opened_at"]) + rows[i]["opened_at"][10:]
    for i in mess.rows(3, 6):
        rows[i]["ticket_id"] = _break_id(rng, rows[i]["ticket_id"])
    _spike(rows, mess.rows(5, 8), "resolution_minutes", rng)
    _typos(rng, rows, mess, "priority", ["Urgent", "hgh"])
    _typos(rng, rows, mess, "status", ["resolvd", "Closed"])
    return _double_entries(rows, mess.rows(*_pct(n, 2, 3)))


# --------------------------------------------------------------------------
# the catalogue
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SampleKind:
    kind: str
    title: str
    description: str
    build: Callable[[random.Random, Faker, int, date], list[dict]]
    columns: tuple[str, ...]
    formats: dict[str, str]  # how numbers are written; everything else is written as it is
    rows: tuple[int, int]  # a sample has a random number of rows between these


SAMPLES: dict[str, SampleKind] = {
    s.kind: s
    for s in [
        SampleKind(
            "orders", "Online store orders",
            "Orders from a web shop, with a few blank emails, odd dates and typos in the status column.",
            _orders, ("order_id", "customer_email", "order_date", "status", "channel", "items", "order_total"),
            {"order_total": "{:.2f}"}, (1800, 2400),
        ),
        SampleKind(
            "customers", "Customer list",
            "A CRM export with missing phone numbers, badly typed emails and a few customers entered twice.",
            _customers, ("customer_id", "full_name", "email", "phone", "signup_date", "plan", "country", "lifetime_value"),
            {"lifetime_value": "{:.2f}"}, (1800, 2400),
        ),
        SampleKind(
            "employees", "Employee records",
            "An HR table with a salary typed with a zero too many, mixed date formats and misspelled departments.",
            _employees, ("employee_id", "full_name", "work_email", "department", "hire_date", "salary", "status"),
            {}, (1300, 1800),
        ),
        SampleKind(
            "invoices", "Supplier invoices",
            "Accounts payable: invoices keyed in twice, amounts that are far too big, and currencies written three ways.",
            _invoices, ("invoice_id", "vendor", "invoice_date", "due_date", "amount", "currency", "status"),
            {"amount": "{:.2f}"}, (1800, 2400),
        ),
        SampleKind(
            "inventory", "Warehouse inventory",
            "Stock counts with a few extra zeros, blank product names and warehouse names that don't match.",
            _inventory, ("product_id", "product_name", "category", "warehouse", "units_in_stock", "unit_price", "last_counted"),
            {"unit_price": "{:.2f}"}, (1500, 2100),
        ),
        SampleKind(
            "tickets", "Support tickets",
            "Customer support tickets with blank agents, resolution times that can't be right, and priority typos.",
            _tickets, ("ticket_id", "opened_at", "customer_email", "channel", "priority", "status", "agent", "resolution_minutes"),
            {}, (2000, 2600),
        ),
    ]
}


@dataclass(frozen=True)
class Sample:
    kind: SampleKind
    seed: int
    columns: list[str]
    rows: list[list[str]]  # every row as the text that goes in the CSV

    @property
    def dataset_name(self) -> str:
        return f"{SAMPLE_DATASET_PREFIX}{self.kind.kind}"

    @property
    def filename(self) -> str:
        return f"{self.dataset_name}.csv"

    def to_csv(self) -> str:
        out = io.StringIO()
        writer = csv.writer(out, lineterminator="\n")
        writer.writerow(self.columns)
        writer.writerows(self.rows)
        return out.getvalue()


def other_kind(exclude: str | None, rng: random.Random | None = None) -> str:
    """A random kind of sample, not the one just shown (so two clicks in a row never repeat)."""
    choices = [k for k in SAMPLES if k != exclude] or list(SAMPLES)
    return (rng or random).choice(choices)


def build_sample(kind: str, seed: int | None = None, rows: int | None = None, today: date | None = None) -> Sample:
    """
    The sample of this kind for this seed (a random one if none is given).
    `rows` fixes the size before the problems are added - the tests use a small
    one; the page lets each sample pick its own.
    """
    spec = SAMPLES[kind]
    seed = random.randrange(1, 2**31) if seed is None else seed
    rng = random.Random(f"{kind}:{seed}")
    fake = Faker("en_US")
    fake.seed_instance(seed)
    n = rows if rows is not None else rng.randint(*spec.rows)

    records = spec.build(rng, fake, n, today or date.today())

    def cell(column: str, value: object) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        return spec.formats[column].format(value) if column in spec.formats else str(value)

    table = [[cell(column, record[column]) for column in spec.columns] for record in records]
    return Sample(kind=spec, seed=seed, columns=list(spec.columns), rows=table)
