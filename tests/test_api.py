import os
import time
from datetime import datetime, timezone, timedelta, date
from decimal import Decimal
import pytest
from fastapi.testclient import TestClient
from pymongo import MongoClient

from app.main import app, db, IST, compute_late_minutes, compute_work_hours, compute_overtime, compute_half_day

client = TestClient(app)

@pytest.fixture(autouse=True)
def setup_teardown():
    # Run before each test
    # Ensure indexes are initialized
    from app.main import init_indexes
    init_indexes()
    yield

def test_health():
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}

def test_create_employee_success():
    emp_code = f"EMP{int(time.time() * 1000) % 90000 + 10000}"
    payload = {
        "emp_code": emp_code,
        "name": "Test User",
        "email": f"test_{emp_code}@example.com",
        "department": "Engineering",
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-01-05"
    }
    res = client.post("/employees", json=payload)
    assert res.status_code == 201
    data = res.json()
    assert data["emp_code"] == emp_code
    assert isinstance(data["created_at"], int)
    assert "_id" not in data

def test_create_employee_duplicate():
    emp_code = f"EMP{int(time.time() * 1000) % 90000 + 10000}"
    payload = {
        "emp_code": emp_code,
        "name": "Dup User",
        "email": f"dup_{emp_code}@example.com",
        "department": "Sales",
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-01-05"
    }
    res1 = client.post("/employees", json=payload)
    assert res1.status_code == 201

    res2 = client.post("/employees", json=payload)
    assert res2.status_code == 409

def test_create_employee_validation_errors():
    # shift_start == shift_end
    res = client.post("/employees", json={
        "emp_code": "EMP8888",
        "name": "Invalid Shift",
        "email": "inv@example.com",
        "department": "IT",
        "shift_start": "09:30",
        "shift_end": "09:30",
        "joined_on": "2026-01-05"
    })
    assert res.status_code == 422

    # Invalid emp_code format
    res = client.post("/employees", json={
        "emp_code": "BADCODE",
        "name": "Invalid Code",
        "email": "inv@example.com",
        "department": "IT",
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-01-05"
    })
    assert res.status_code == 422

def test_list_employees_pagination_and_filter():
    res = client.get("/employees?page=1&page_size=2")
    assert res.status_code == 200
    data = res.json()
    assert "items" in data
    assert "total" in data
    assert data["page"] == 1
    assert data["page_size"] == 2
    assert len(data["items"]) <= 2

    # Filter department
    res_dept = client.get("/employees?department=Engineering")
    assert res_dept.status_code == 200
    data_dept = res_dept.json()
    for item in data_dept["items"]:
        assert item["department"] == "Engineering"

def test_r2_late_minutes_rules():
    # Shift 09:30 IST: 09:40:00 -> 0; 09:40:01 -> 10; 10:15:59 -> 45
    d = "2026-07-06"
    dt_094000 = datetime(2026, 7, 6, 9, 40, 0, tzinfo=IST).astimezone(timezone.utc)
    assert compute_late_minutes(dt_094000, "09:30", d) == 0

    dt_094001 = datetime(2026, 7, 6, 9, 40, 1, tzinfo=IST).astimezone(timezone.utc)
    assert compute_late_minutes(dt_094001, "09:30", d) == 10

    dt_101559 = datetime(2026, 7, 6, 10, 15, 59, tzinfo=IST).astimezone(timezone.utc)
    assert compute_late_minutes(dt_101559, "09:30", d) == 45

def test_r3_r4_r5_derived_calculations():
    # 09:28 to 18:35: 9.12 hours, 5 min overtime (< 30) -> 0
    t_in = datetime(2026, 7, 6, 9, 28, 0, tzinfo=IST).astimezone(timezone.utc)
    t_out = datetime(2026, 7, 6, 18, 35, 0, tzinfo=IST).astimezone(timezone.utc)
    hours = compute_work_hours(t_in, t_out)
    assert hours == 9.12
    assert compute_half_day(hours) is False
    assert compute_overtime(t_out, "09:30", "18:30", "2026-07-06") == 0

    # 40 min overtime
    t_out_ot = datetime(2026, 7, 6, 19, 10, 0, tzinfo=IST).astimezone(timezone.utc)
    assert compute_overtime(t_out_ot, "09:30", "18:30", "2026-07-06") == 40

    # Half day
    t_out_half = datetime(2026, 7, 6, 13, 0, 0, tzinfo=IST).astimezone(timezone.utc)
    h_half = compute_work_hours(t_in, t_out_half)
    assert h_half < 4.50
    assert compute_half_day(h_half) is True

def test_punch_in_and_punch_out_flow():
    emp_code = f"EMP{int(time.time() * 1000) % 90000 + 10000}"
    client.post("/employees", json={
        "emp_code": emp_code,
        "name": "Flow Tester",
        "email": f"flow_{emp_code}@example.com",
        "department": "Engineering",
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-01-05"
    })

    # Punch in on 2026-08-03 at 09:45:00 IST (15 min late)
    # 09:45 IST is 04:15 UTC
    t_in_dt = datetime(2026, 8, 3, 9, 45, 0, tzinfo=IST)
    ms_in = int(t_in_dt.astimezone(timezone.utc).timestamp() * 1000)

    res_in = client.post("/attendance/punch-in", json={
        "emp_code": emp_code,
        "punched_at": ms_in,
        "status": "PRESENT"
    })
    assert res_in.status_code == 201
    doc_in = res_in.json()
    assert doc_in["emp_code"] == emp_code
    assert doc_in["date"] == "2026-08-03"
    assert doc_in["late_minutes"] == 15
    assert doc_in["punch_out"] is None
    assert doc_in["work_hours"] is None
    assert "_id" not in doc_in
    assert "id" not in doc_in

    # Duplicate punch in for same day must be 409
    res_dup = client.post("/attendance/punch-in", json={
        "emp_code": emp_code,
        "punched_at": ms_in,
        "status": "PRESENT"
    })
    assert res_dup.status_code == 409

    # Punch out at 19:15:00 IST (45 min overtime, 9.5 hours)
    t_out_dt = datetime(2026, 8, 3, 19, 15, 0, tzinfo=IST)
    ms_out = int(t_out_dt.astimezone(timezone.utc).timestamp() * 1000)

    res_out = client.post("/attendance/punch-out", json={
        "emp_code": emp_code,
        "punched_at": ms_out
    })
    assert res_out.status_code == 200
    doc_out = res_out.json()
    assert doc_out["punch_out"] == ms_out
    assert doc_out["work_hours"] == 9.50
    assert doc_out["overtime_minutes"] == 45
    assert doc_out["half_day"] is False

    # Second punch out for the same record must be 409
    res_out_dup = client.post("/attendance/punch-out", json={
        "emp_code": emp_code,
        "punched_at": ms_out
    })
    assert res_out_dup.status_code == 409

def test_punch_out_errors():
    # Unknown employee -> 404
    res = client.post("/attendance/punch-out", json={
        "emp_code": "EMP0000",
        "punched_at": 1783312500000
    })
    assert res.status_code == 404

def test_list_attendance():
    res = client.get("/attendance?page=1&page_size=5")
    assert res.status_code == 200
    data = res.json()
    assert "items" in data
    assert "total" in data

    # date_from > date_to -> 422
    res_err = client.get("/attendance?date_from=2026-08-10&date_to=2026-08-01")
    assert res_err.status_code == 422

def test_regularize_record_with_history():
    emp_code = f"EMP{int(time.time() * 1000) % 90000 + 10000}"
    client.post("/employees", json={
        "emp_code": emp_code,
        "name": "Reg Tester",
        "email": f"reg_{emp_code}@example.com",
        "department": "Support",
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-01-05"
    })

    t_in_dt = datetime(2026, 8, 4, 10, 15, 0, tzinfo=IST)
    ms_in = int(t_in_dt.astimezone(timezone.utc).timestamp() * 1000)
    client.post("/attendance/punch-in", json={"emp_code": emp_code, "punched_at": ms_in})

    # Correct punch-in to 09:30 (on time)
    new_in_dt = datetime(2026, 8, 4, 9, 30, 0, tzinfo=IST)
    new_ms_in = int(new_in_dt.astimezone(timezone.utc).timestamp() * 1000)

    res_patch = client.patch(f"/attendance/{emp_code}/2026-08-4", json={
        "punch_in": new_ms_in,
        "reason": "Traffic delay approved by manager",
        "regularized_by": "manager.dave"
    })
    # Date in URL must match format
    res_patch = client.patch(f"/attendance/{emp_code}/2026-08-04", json={
        "punch_in": new_ms_in,
        "reason": "Traffic delay approved by manager",
        "regularized_by": "manager.dave"
    })
    assert res_patch.status_code == 200
    doc = res_patch.json()
    assert doc["late_minutes"] == 0
    assert len(doc["history"]) == 1
    assert doc["history"][0]["by"] == "manager.dave"
    assert "late_minutes" in doc["history"][0]["changes"]

    # Request that changes nothing must return 422
    res_noop = client.patch(f"/attendance/{emp_code}/2026-08-04", json={
        "punch_in": new_ms_in,
        "reason": "Redundant correction attempt",
        "regularized_by": "manager.dave"
    })
    assert res_noop.status_code == 422

def test_analytics_employee_monthly():
    # EMP0001 from sample seed
    res = client.get("/analytics/employees/EMP0001/monthly?month=2026-07")
    assert res.status_code == 200
    data = res.json()
    assert data["emp_code"] == "EMP0001"
    assert data["month"] == "2026-07"
    assert data["working_days"] > 0
    assert data["present_days"] >= 0

def test_analytics_department_summary():
    res = client.get("/analytics/departments/summary?month=2026-07")
    assert res.status_code == 200
    data = res.json()
    assert "items" in data
    assert len(data["items"]) > 0
    depts = [item["department"] for item in data["items"]]
    assert "Engineering" in depts

def test_analytics_leaderboard():
    res = client.get("/analytics/leaderboard/late?month=2026-07&limit=5")
    assert res.status_code == 200
    data = res.json()
    assert "items" in data
    if len(data["items"]) > 0:
        assert data["items"][0]["rank"] == 1
        assert data["items"][0]["total_late_minutes"] > 0

def test_analytics_department_trend():
    res = client.get("/analytics/departments/Engineering/trend?from=2026-07-01&to=2026-07-07")
    assert res.status_code == 200
    data = res.json()
    assert data["department"] == "Engineering"
    assert len(data["items"]) == 7
    for item in data["items"]:
        assert "date" in item
        assert "is_working_day" in item
        assert "headcount" in item

def test_admin_explain_endpoints():
    endpoints = [
        ("attendance_list", "?page=1&page_size=10"),
        ("employee_monthly", "?emp_code=EMP0001&month=2026-07"),
        ("department_summary", "?month=2026-07"),
        ("late_leaderboard", "?month=2026-07"),
        ("department_trend", "?department=Engineering&from=2026-07-01&to=2026-07-07"),
    ]
    for ep, params in endpoints:
        res = client.get(f"/admin/explain/{ep}{params}")
        assert res.status_code == 200, f"Explain failed for {ep}: {res.text}"
        data = res.json()
        assert data["endpoint"] == ep
        assert "collection" in data
        assert "explain" in data
        explain_str = str(data["explain"])
        assert "IXSCAN" in explain_str, f"No IXSCAN in {ep}"
        assert "COLLSCAN" not in explain_str, f"COLLSCAN found in {ep}"

def test_overnight_shift_r1():
    # EMP0005 has overnight shift 22:00 - 06:00
    emp_code = f"EMP{int(time.time() * 1000) % 90000 + 10000}"
    client.post("/employees", json={
        "emp_code": emp_code,
        "name": "Night Owl",
        "email": f"night_{emp_code}@example.com",
        "department": "Support",
        "shift_start": "22:00",
        "shift_end": "06:00",
        "joined_on": "2026-01-05"
    })

    # Punch-in at 05:45 IST on 2026-07-07: earlier than 06:00 -> belongs to 2026-07-06 shift!
    t_early = datetime(2026, 7, 7, 5, 45, 0, tzinfo=IST)
    ms_early = int(t_early.astimezone(timezone.utc).timestamp() * 1000)
    res = client.post("/attendance/punch-in", json={"emp_code": emp_code, "punched_at": ms_early})
    assert res.status_code == 201
    assert res.json()["date"] == "2026-07-06"

    # Punch-in at 06:15 IST on 2026-07-07: >= 06:00 -> belongs to 2026-07-07 shift!
    t_normal = datetime(2026, 7, 7, 6, 15, 0, tzinfo=IST)
    ms_normal = int(t_normal.astimezone(timezone.utc).timestamp() * 1000)
    res2 = client.post("/attendance/punch-in", json={"emp_code": emp_code, "punched_at": ms_normal})
    assert res2.status_code == 201
    assert res2.json()["date"] == "2026-07-07"

def test_concurrency_simultaneous_punch_in():
    import concurrent.futures
    emp_code = f"EMP{int(time.time() * 1000) % 90000 + 10000}"
    client.post("/employees", json={
        "emp_code": emp_code,
        "name": "Race Tester",
        "email": f"race_{emp_code}@example.com",
        "department": "Sales",
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-01-05"
    })

    t_in = datetime(2026, 9, 1, 9, 30, 0, tzinfo=IST)
    ms_in = int(t_in.astimezone(timezone.utc).timestamp() * 1000)

    results = []
    def make_punch():
        return client.post("/attendance/punch-in", json={"emp_code": emp_code, "punched_at": ms_in})

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(make_punch) for _ in range(5)]
        for f in concurrent.futures.as_completed(futures):
            results.append(f.result().status_code)

    # Exactly one request must get 201 and the others must get 409
    assert results.count(201) == 1
    assert results.count(409) == 4

def test_concurrency_simultaneous_punch_out():
    import concurrent.futures
    emp_code = f"EMP{int(time.time() * 1000) % 90000 + 10000}"
    client.post("/employees", json={
        "emp_code": emp_code,
        "name": "Out Race Tester",
        "email": f"out_race_{emp_code}@example.com",
        "department": "Sales",
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-01-05"
    })

    t_in = datetime(2026, 9, 2, 9, 30, 0, tzinfo=IST)
    ms_in = int(t_in.astimezone(timezone.utc).timestamp() * 1000)
    client.post("/attendance/punch-in", json={"emp_code": emp_code, "punched_at": ms_in})

    t_out = datetime(2026, 9, 2, 18, 30, 0, tzinfo=IST)
    ms_out = int(t_out.astimezone(timezone.utc).timestamp() * 1000)

    results = []
    def make_punch_out():
        return client.post("/attendance/punch-out", json={"emp_code": emp_code, "punched_at": ms_out})

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(make_punch_out) for _ in range(5)]
        for f in concurrent.futures.as_completed(futures):
            results.append(f.result().status_code)

    # Exactly one request gets 200 and the others get 409
    assert results.count(200) == 1
    assert results.count(409) == 4

def test_regularize_absent_and_leave_rules():
    emp_code = f"EMP{int(time.time() * 1000) % 90000 + 10000}"
    client.post("/employees", json={
        "emp_code": emp_code,
        "name": "Absence Tester",
        "email": f"abs_{emp_code}@example.com",
        "department": "IT",
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-01-05"
    })

    t_in = datetime(2026, 9, 3, 9, 30, 0, tzinfo=IST)
    ms_in = int(t_in.astimezone(timezone.utc).timestamp() * 1000)
    client.post("/attendance/punch-in", json={"emp_code": emp_code, "punched_at": ms_in})

    # Supplying punch times with ABSENT is forbidden -> 422
    res_bad = client.patch(f"/attendance/{emp_code}/2026-09-03", json={
        "status": "ABSENT",
        "punch_in": ms_in,
        "reason": "Marking absent",
        "regularized_by": "admin"
    })
    assert res_bad.status_code == 422

    # Setting ABSENT clears punch times and sets work_hours to null
    res_absent = client.patch(f"/attendance/{emp_code}/2026-09-03", json={
        "status": "ABSENT",
        "reason": "Employee actually absent",
        "regularized_by": "admin"
    })
    assert res_absent.status_code == 200
    doc = res_absent.json()
    assert doc["status"] == "ABSENT"
    assert doc["punch_in"] is None
    assert doc["punch_out"] is None
    assert doc["work_hours"] is None
    assert doc["late_minutes"] == 0

def test_trend_range_limit_and_order():
    # Date range > 92 days -> 422
    res = client.get("/analytics/departments/Engineering/trend?from=2026-01-01&to=2026-06-01")
    assert res.status_code == 422

    # to < from -> 422
    res2 = client.get("/analytics/departments/Engineering/trend?from=2026-07-10&to=2026-07-05")
    assert res2.status_code == 422

    # Non-existent department -> 404
    res3 = client.get("/analytics/departments/NonExistentDept/trend?from=2026-07-01&to=2026-07-05")
    assert res3.status_code == 404

def test_headcount_zero_logs_and_leaderboard_ties():
    # 1. Create a brand new department with 2 employees
    dept = f"Dept_{int(time.time() * 1000) % 90000}"
    emp1 = f"EMP{int(time.time() * 1000) % 90000 + 10000}"
    emp2 = f"EMP{int(time.time() * 1000) % 90000 + 10001}"

    client.post("/employees", json={
        "emp_code": emp1,
        "name": "Zero Log Emp 1",
        "email": f"{emp1}@example.com",
        "department": dept,
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-05-01"
    })
    client.post("/employees", json={
        "emp_code": emp2,
        "name": "Zero Log Emp 2",
        "email": f"{emp2}@example.com",
        "department": dept,
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-05-15"
    })

    # Department summary for 2026-05: both employees have 0 logs, headcount must still be 2!
    res_ds = client.get(f"/analytics/departments/summary?month=2026-05&department={dept}")
    assert res_ds.status_code == 200
    ds_data = res_ds.json()
    assert len(ds_data["items"]) == 1
    assert ds_data["items"][0]["department"] == dept
    assert ds_data["items"][0]["headcount"] == 2
    assert ds_data["items"][0]["present_days"] == 0.0
    assert ds_data["items"][0]["avg_work_hours"] is None

    # Employee 2 joined mid-month on 2026-05-15. Check working days in May 2026
    res_m = client.get(f"/analytics/employees/{emp2}/monthly?month=2026-05")
    assert res_m.status_code == 200
    # From May 15, 2026 to May 31, 2026
    assert res_m.json()["working_days"] == 11
    assert res_m.json()["attendance_pct"] == 0.0

    # 2. Test Leaderboard ties: punch-in both with exact same late minutes
    t_in1 = datetime(2026, 5, 18, 10, 0, 0, tzinfo=IST)
    t_in2 = datetime(2026, 5, 19, 10, 0, 0, tzinfo=IST)
    ms_in1 = int(t_in1.astimezone(timezone.utc).timestamp() * 1000)
    ms_in2 = int(t_in2.astimezone(timezone.utc).timestamp() * 1000)

    client.post("/attendance/punch-in", json={"emp_code": emp1, "punched_at": ms_in1})
    client.post("/attendance/punch-in", json={"emp_code": emp2, "punched_at": ms_in2})

    # Both have 30 late minutes in 2026-05!
    res_lb = client.get(f"/analytics/leaderboard/late?month=2026-05&department={dept}&limit=1")
    assert res_lb.status_code == 200
    lb_data = res_lb.json()
    # Since both tied at rank 1, limit=1 returns both rows (rank <= 1)!
    assert len(lb_data["items"]) == 2
    assert lb_data["items"][0]["rank"] == 1
    assert lb_data["items"][1]["rank"] == 1
    assert lb_data["items"][0]["total_late_minutes"] == 30
    assert lb_data["items"][1]["total_late_minutes"] == 30
    # Ordered by emp_code ascending among ties
    assert lb_data["items"][0]["emp_code"] < lb_data["items"][1]["emp_code"]

def test_string_epoch_and_bad_date_validation():
    # Sending string for punched_at must be rejected with 422 per OpenAPI spec
    res = client.post("/attendance/punch-in", json={
        "emp_code": "EMP0001",
        "punched_at": "1783312500000"
    })
    assert res.status_code == 422

    # Bad date format in query params
    res_q = client.get("/attendance?date_from=not-a-date")
    assert res_q.status_code == 422

    # Bad date format in path
    res_p = client.patch("/attendance/EMP0001/not-a-date", json={
        "reason": "testing bad date in path",
        "regularized_by": "admin"
    })
    assert res_p.status_code == 422


def test_sample_data_independent_calculations():
    import pathlib
    from bson import json_util
    seed_dir = pathlib.Path(__file__).parent.parent / "sample_data"
    opts = json_util.JSONOptions(tz_aware=True)
    for name in ("employees", "attendance_logs"):
        docs = json_util.loads((seed_dir / f"{name}.json").read_text(), json_options=opts)
        db[name].delete_many({})
        db[name].insert_many(docs)

    # 1. Employee Monthly
    r1 = client.get("/analytics/employees/EMP0001/monthly?month=2026-07").json()
    assert r1["working_days"] == 23
    assert r1["present_days"] == 2.0
    assert r1["attendance_pct"] == 8.70

    r2 = client.get("/analytics/employees/EMP0002/monthly?month=2026-07").json()
    assert r2["working_days"] == 15
    assert r2["present_days"] == 1.0
    assert r2["attendance_pct"] == 6.67

    r3 = client.get("/analytics/employees/EMP0003/monthly?month=2026-07").json()
    assert r3["working_days"] == 23
    assert r3["present_days"] == 1.0
    assert r3["leave_days"] == 1
    assert r3["late_count"] == 1
    assert r3["total_late_minutes"] == 35
    assert r3["total_overtime_minutes"] == 40
    assert r3["attendance_pct"] == 4.35

    # 2. Department Summary
    ds = client.get("/analytics/departments/summary?month=2026-07").json()
    eng = next(item for item in ds["items"] if item["department"] == "Engineering")
    assert eng["headcount"] == 2
    assert eng["present_days"] == 3.0
    assert eng["avg_work_hours"] == 9.04

    sales = next(item for item in ds["items"] if item["department"] == "Sales")
    assert sales["headcount"] == 2
    assert sales["present_days"] == 1.5
    assert sales["avg_work_hours"] == 6.29
    assert sales["late_count"] == 1
    assert sales["total_late_minutes"] == 35
    assert sales["leave_count"] == 1

    support = next(item for item in ds["items"] if item["department"] == "Support")
    assert support["headcount"] == 2
    assert support["present_days"] == 2.0
    assert support["avg_work_hours"] == 8.75

    # 3. Leaderboard
    lb = client.get("/analytics/leaderboard/late?month=2026-07").json()
    assert len(lb["items"]) == 1
    assert lb["items"][0]["emp_code"] == "EMP0003"
    assert lb["items"][0]["rank"] == 1
    assert lb["items"][0]["total_late_minutes"] == 35

    # 4. Department Trend
    tr = client.get("/analytics/departments/Sales/trend?from=2026-07-06&to=2026-07-08").json()
    assert len(tr["items"]) == 3
    assert tr["items"][0]["date"] == "2026-07-06"
    assert tr["items"][0]["attendance_rate"] == 0.5000
    assert tr["items"][0]["moving_avg_7d"] == 0.5000
    assert tr["items"][1]["date"] == "2026-07-07"
    assert tr["items"][1]["attendance_rate"] == 0.2500
    assert tr["items"][1]["moving_avg_7d"] == 0.3750
    assert tr["items"][2]["date"] == "2026-07-08"
    assert tr["items"][2]["attendance_rate"] == 0.0000
    assert tr["items"][2]["moving_avg_7d"] == 0.2500


def test_edge_case_not_found_and_validation_errors():
    assert client.get("/analytics/employees/EMP9999/monthly?month=2026-07").status_code == 404
    assert client.patch("/attendance/EMP9999/2026-07-06", json={"reason": "test reason", "regularized_by": "admin"}).status_code == 404
    assert client.patch("/attendance/EMP0001/2026-01-01", json={"reason": "test reason", "regularized_by": "admin"}).status_code == 404
    assert client.get("/admin/explain/unknown_endpoint").status_code == 422
    assert client.get("/admin/explain/employee_monthly").status_code == 422
    assert client.get("/admin/explain/department_trend").status_code == 422
    assert client.get("/employees?page=0").status_code == 422
    assert client.get("/employees?page_size=101").status_code == 422




