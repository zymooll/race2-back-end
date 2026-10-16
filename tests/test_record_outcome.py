"""真实结果回填：本人权限、管理员兼容与持久化契约。"""

from uuid import uuid4

import pytest

from app.core.security import create_access_token
from app.db.session import SessionLocal
from app.models.assessment import AssessmentRecord
from app.models.user import User


@pytest.fixture
def outcome_records():
    with SessionLocal() as db:
        suffix = uuid4().hex[:12]
        owner = User(username=f"outcome_owner_{suffix}", password_hash="unused", role="analyst", status=1)
        other = User(username=f"outcome_other_{suffix}", password_hash="unused", role="analyst", status=1)
        db.add_all([owner, other])
        db.flush()
        records = {}
        for name, user_id in (("own", owner.id), ("other", other.id), ("anonymous", None)):
            rec = AssessmentRecord(
                enterprise_name=f"回填权限测试_{suffix}_{name}",
                business_type="01",
                user_id=user_id,
                score=620,
                probability=0.2,
                level="中等风险",
                result_json={"score": 620, "advice": "原始评估结果"},
            )
            db.add(rec)
            db.flush()
            records[name] = rec.id
        db.commit()
        return {
            **records,
            "headers": {"Authorization": f"Bearer {create_access_token(owner.username, owner.role)}"},
        }


@pytest.mark.parametrize("outcome", ["normal", "overdue", "rejected", "pending"])
def test_owner_can_fill_and_reload_outcome(client, outcome_records, outcome):
    record_id = outcome_records["own"]
    response = client.put(
        f"/api/v1/risk/records/{record_id}/outcome",
        headers=outcome_records["headers"],
        json={"outcome": outcome, "note": "  实际还款回访  "},
    ).json()
    assert response["code"] == 200
    updated = response["data"]
    assert updated["outcome"] == outcome
    assert updated["outcomeNote"] == "实际还款回访"
    assert updated["outcomeAt"]
    assert updated["score"] == 620
    assert updated["result"] == {"score": 620, "advice": "原始评估结果"}

    detail = client.get(f"/api/v1/risk/records/{record_id}", headers=outcome_records["headers"]).json()["data"]
    records = client.get("/api/v1/risk/records", headers=outcome_records["headers"]).json()["data"]["items"]
    listed = next(row for row in records if row["id"] == record_id)
    for row in (detail, listed):
        assert row["outcome"] == outcome
        assert row["outcomeNote"] == updated["outcomeNote"]
        assert row["outcomeAt"] == updated["outcomeAt"]


@pytest.mark.parametrize("target", ["other", "anonymous"])
def test_analyst_cannot_fill_other_or_anonymous_record(client, outcome_records, target):
    record_id = outcome_records[target]
    body = client.put(
        f"/api/v1/risk/records/{record_id}/outcome",
        headers=outcome_records["headers"],
        json={"outcome": "overdue", "note": "不应保存"},
    ).json()
    assert body["code"] == 403
    with SessionLocal() as db:
        rec = db.get(AssessmentRecord, record_id)
        assert rec.outcome == "pending"
        assert rec.outcome_note is None
        assert rec.outcome_at is None


@pytest.mark.parametrize("target", ["other", "anonymous"])
def test_admin_can_still_fill_all_records(client, auth_headers, outcome_records, target):
    body = client.put(
        f"/api/v1/risk/records/{outcome_records[target]}/outcome",
        headers=auth_headers,
        json={"outcome": "normal", "note": "管理员核实"},
    ).json()
    assert body["code"] == 200
    assert body["data"]["outcome"] == "normal"
    assert body["data"]["outcomeNote"] == "管理员核实"


def test_outcome_requires_login(client, outcome_records):
    body = client.put(f"/api/v1/risk/records/{outcome_records['own']}/outcome", json={"outcome": "normal"}).json()
    assert body["code"] == 401


def test_outcome_rejects_missing_record(client, outcome_records):
    body = client.put(
        "/api/v1/risk/records/2147483647/outcome",
        headers=outcome_records["headers"],
        json={"outcome": "normal"},
    ).json()
    assert body["code"] == 404


def test_outcome_rejects_invalid_result(client, outcome_records):
    body = client.put(
        f"/api/v1/risk/records/{outcome_records['own']}/outcome",
        headers=outcome_records["headers"],
        json={"outcome": "invalid"},
    ).json()
    assert body["code"] == 422
