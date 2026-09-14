from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.database import session
from app.models import Dedupe, Metric, PipelineState, Usage, now


def metric(db, source, name, amount=1):
    current = now()
    stmt = insert(Metric).values(day=current.date(), source=source, name=name, value=amount)
    db.execute(
        stmt.on_conflict_do_update(index_elements=["day", "source", "name"], set_={"value": Metric.value + amount})
    )
    # The aggregate raw counter commits in the same transaction as accepted source data and cursor state.
    if source == "all" and name == "raw_scanned" and amount > 0:
        state = db.get(PipelineState, 1)
        if state is not None and state.campaign_status == "RUNNING":
            state.campaign_raw_scanned += int(amount)
            state.campaign_last_progress_at = current
            if state.campaign_raw_target and state.campaign_raw_scanned >= state.campaign_raw_target:
                state.campaign_status = "DRAINING"
                state.draining = True


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


def reserve_budget(provider, units, unit_cap, token_cap=None):
    """Atomically reserve attempts and optionally stop once already-accounted tokens reach a soft cap."""
    day = usage_day(provider)
    with session() as db:
        db.execute(
            insert(Usage)
            .values(day=day, provider=provider, units=0, input_tokens=0, output_tokens=0, estimated_cost=0)
            .on_conflict_do_nothing()
        )
        row = db.scalar(select(Usage).where(Usage.day == day, Usage.provider == provider).with_for_update())
        if token_cap is not None and row.input_tokens + row.output_tokens >= token_cap:
            return "token_cap"
        if row.units + units > unit_cap:
            return "unit_cap"
        # Committed before the HTTP call, including failed calls. Crashes cannot reset caps.
        row.units += units
    return "ok"


def reserve(provider, units, cap):
    return reserve_budget(provider, units, cap) == "ok"


def account_tokens(provider, incoming, outgoing, cost):
    with session() as db:
        row = db.scalar(
            select(Usage).where(Usage.day == usage_day(provider), Usage.provider == provider).with_for_update()
        )
        if row:
            row.input_tokens += incoming
            row.output_tokens += outgoing
            row.estimated_cost += cost
