"""
Generates a clean synthetic `customers` dataset, then writes a corrupted
copy with a known, counted set of data-quality issues injected into it.

The point: every number the API reports back (missing values, duplicates,
invalid IDs) can be checked against the ground truth this script prints.
That's what makes "14,283 records affected" a real, reproducible number
instead of one made up for a demo screenshot.

Run from the project root:
    python -m scripts.seed_dataset
"""
import os
import random

import pandas as pd
from faker import Faker

fake = Faker()
Faker.seed(42)
random.seed(42)

N_ROWS = 20_000
OUT_DIR = "data"

_PLANS = ["basic", "pro", "enterprise"]
_PLAN_WEIGHTS = [0.6, 0.3, 0.1]


def generate_clean_customers(n: int = N_ROWS) -> pd.DataFrame:
    rows = []
    for i in range(1, n + 1):
        rows.append(
            {
                "customer_id": f"CUST-{i:06d}",
                "name": fake.name(),
                "email": fake.unique.email(),
                "phone": fake.phone_number(),
                "signup_date": fake.date_between(start_date="-3y", end_date="today"),
                "lifetime_value": round(max(0, random.gauss(450, 120)), 2),
                "country": fake.country_code(),
                "plan": random.choices(_PLANS, weights=_PLAN_WEIGHTS, k=1)[0],
            }
        )
    return pd.DataFrame(rows)


def generate_orders(customers: pd.DataFrame, n: int = 500) -> pd.DataFrame:
    """
    A small synthetic orders dataset referencing customers by customer_id -
    exists to demonstrate the broken_relationships check, which (unlike the
    other seven) needs a second, related dataset to check against. ~3% of
    orders reference a customer_id that was never a real customer.
    """
    valid_ids = customers["customer_id"].tolist()
    rows = []
    for i in range(1, n + 1):
        rows.append(
            {
                "order_id": f"ORD-{i:06d}",
                "customer_id": random.choice(valid_ids),
                "amount": round(max(0, random.gauss(80, 25)), 2),
                "order_date": fake.date_between(start_date="-1y", end_date="today"),
            }
        )
    df = pd.DataFrame(rows)

    broken_idx = df.sample(frac=0.03, random_state=5).index
    df.loc[broken_idx, "customer_id"] = "CUST-999999"
    return df


def corrupt_dataset(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Returns a corrupted copy plus a dict of exactly how many rows each issue touched."""
    df = df.copy()

    # --- Missing values: null out email on ~4% of rows ---
    missing_idx = df.sample(frac=0.04, random_state=1).index
    df.loc[missing_idx, "email"] = None

    # --- Duplicates: append a wholesale copy of ~2% of rows ---
    dup_rows = df.sample(frac=0.02, random_state=2)
    df = pd.concat([df, dup_rows], ignore_index=True)

    # --- Invalid formats: strip the "CUST-" prefix on ~3% of rows ---
    invalid_idx = df.sample(frac=0.03, random_state=3).index
    df.loc[invalid_idx, "customer_id"] = df.loc[invalid_idx, "customer_id"].astype(str).str.replace(
        "CUST-", "", regex=False
    )

    # --- Unexpected values: typo a single row's plan outside the normal set ---
    stray_idx = df.sample(n=1, random_state=4).index
    df.loc[stray_idx, "plan"] = "Basicc"

    # Ground truth is measured from the FINAL dataframe, not bookkept through
    # the mutation steps above - those steps can interact (e.g. a row that
    # loses its email can also get duplicated, carrying the null along), and
    # counting incrementally undercounts when that happens. Measuring the
    # end state directly is what keeps this number honest.
    #
    # "duplicates" is measured as a full-row match (every column), matching
    # what the generic DuplicatesCheck does by default now that it isn't
    # hardcoded to a specific column subset for this schema.
    counts = {
        "missing_values": int(df["email"].isna().sum()),
        "duplicates": int(df.duplicated(keep=False).sum()),
        "invalid_customer_id": int((~df["customer_id"].astype(str).str.match(r"^CUST-\d{6}$")).sum()),
        "unexpected_plan_value": int((df["plan"] == "Basicc").sum()),
    }

    return df, counts


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    clean = generate_clean_customers()
    clean_path = os.path.join(OUT_DIR, "customers_clean.csv")
    clean.to_csv(clean_path, index=False)

    corrupted, counts = corrupt_dataset(clean)
    seeded_path = os.path.join(OUT_DIR, "customers_seeded.csv")
    corrupted.to_csv(seeded_path, index=False)

    orders = generate_orders(clean)
    orders_path = os.path.join(OUT_DIR, "orders_seeded.csv")
    orders.to_csv(orders_path, index=False)

    print(f"Clean dataset:    {clean_path}  ({len(clean):,} rows)")
    print(f"Seeded dataset:   {seeded_path}  ({len(corrupted):,} rows)")
    print(f"Orders dataset:   {orders_path}  ({len(orders):,} rows) - upload alongside customers_seeded.csv")
    print("                  to see the broken_relationships check (relate its customer_id to customers_seeded.csv's)")
    print()
    print("Known injected issues (ground truth):")
    for issue, count in counts.items():
        print(f"  {issue:<22} {count:,} rows")


if __name__ == "__main__":
    main()
