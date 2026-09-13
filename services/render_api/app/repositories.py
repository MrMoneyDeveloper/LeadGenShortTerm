from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.database import session
from app.models import Dedupe, Metric, Usage, now


def metric(db, source, name, amount=1):
    stmt = insert(Metric).values(day=now().date(), source=source, name=name, value=amount)
    db.execute(
        stmt.on_conflict_do_update(index_elements=["day", "source", "name"], set_={"value": Metric.value + amount})
    )


def claim_hashes(db, keys):
    # Savepoint makes multi-hash admission all-or-nothing.
    with db.begin_nested() as savepoint:
        for key in sorted(set(keys)):
            result = db.execute(insert(Dedupe).values(key=key).on_conflict_do_nothing().returning(Dedupe.key)).scalar()
            if result is None:
                savepoint.rollback()
                return False
    return True


def usage_day(provider):
    zone = ZoneInfo("America/Los_Angeles") if provider.startswith("youtube") else ZoneInfo("UTC")
    return datetime.now(zone).date()


def reserve(provider, units, cap):
    # Committed before the HTTP call, including failed calls. Crashes cannot reset caps.
    day = usage_day(provider)
    with session() as db:
        db.execute(
            insert(Usage)
            .values(day=day, provider=provider, units=0, input_tokens=0, output_tokens=0, estimated_cost=0)
            .on_conflict_do_nothing()
        )
        row = db.scalar(select(Usage).where(Usage.day == day, Usage.provider == provider).with_for_update())
        if row.units + units > cap:
            return False
        row.units += units
    return True


def account_tokens(provider, incoming, outgoing, cost):
    with session() as db:
        row = db.scalar(
            select(Usage).where(Usage.day == usage_day(provider), Usage.provider == provider).with_for_update()
        )
        if row:
            row.input_tokens += incoming
            row.output_tokens += outgoing
            row.estimated_cost += cost
