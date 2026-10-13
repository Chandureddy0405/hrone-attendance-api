# Employee Attendance & Analytics API

A robust, production-conscious FastAPI and MongoDB backend service for managing employee attendance, handling punch-in/out workflows, manual audit-logged regularizations, and generating department-level analytics using MongoDB aggregation pipelines.

---

## Technology Stack

- **Python**: 3.11+
- **Framework**: FastAPI 0.110+
- **ASGI Server**: Uvicorn 0.29+
- **Database**: MongoDB 6.0+ (Tested on MongoDB 8.2.7)
- **Driver**: PyMongo 4.7+
- **Validation**: Pydantic v2
- **Testing**: Pytest & HTTPX

---

## Prerequisites

- Python 3.11 or newer
- MongoDB 6.0 or newer running locally or accessible via network URI

---

## Installation & Setup

1. **Clone the repository and enter directory**:
   ```bash
   git clone <REPO_URL>
   cd candidate_kit
   ```

2. **Create and activate a virtual environment**:
   ```bash
   python -m venv .venv
   # Windows PowerShell:
   .venv\Scripts\Activate.ps1
   # Linux / macOS:
   source .venv/bin/activate
   ```

3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment variables**:
   Create a `.env` file (or export shell variables):
   ```bash
   MONGO_URI=mongodb://<DB_USER>:<DB_PASSWORD>@<DB_HOST>:<DB_PORT>/?authSource=admin
   MONGO_DB=attendance_db
   ```
   *Defaults to `mongodb://localhost:27017` and `attendance_db` if not specified.*

5. **Seed sample data (optional)**:
   ```bash
   python sample_seed.py
   ```

6. **Start the API service**:
   ```bash
   uvicorn app.main:app --port 8000 --reload
   ```

---

## API Documentation & Endpoints

Interactive Swagger UI documentation is available at:
- **Swagger UI**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **ReDoc**: [http://localhost:8000/redoc](http://localhost:8000/redoc)

### Implemented Routes

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Readiness probe; verifies MongoDB connection |
| `POST` | `/employees` | Register new employee (unique `emp_code`, validation) |
| `GET` | `/employees` | List employees (department filter, sorted by `emp_code`, paginated) |
| `POST` | `/attendance/punch-in` | Record punch-in (R1 IST date, R2 late minutes, concurrency safe) |
| `POST` | `/attendance/punch-out` | Record punch-out (computes R3 overtime, R4 work hours, R5 half day) |
| `GET` | `/attendance` | List attendance records (filtering, sorting, paginated) |
| `PATCH` | `/attendance/{emp_code}/{date}` | Regularize attendance record with audit history |
| `GET` | `/analytics/employees/{emp_code}/monthly` | Monthly employee attendance breakdown (aggregation) |
| `GET` | `/analytics/departments/summary` | Department summary with headcount including 0-log staff (aggregation) |
| `GET` | `/analytics/leaderboard/late` | Late leaderboard with standard competition ranking ties (window `$rank`) |
| `GET` | `/analytics/departments/{department}/trend` | 7-day moving average trend with gap-filling (window `$densify`) |
| `GET` | `/admin/explain/{endpoint}` | MongoDB query planner & execution stats explain endpoint |

---

## Running Automated Tests

Run the test suite using `pytest`:
```bash
pytest -v
```

---

## Key Implementation Assumptions & Decisions

1. **Natural Key Identification**: Attendance records are addressed by `(emp_code, date)`. MongoDB internal `_id` is never accepted or returned in API responses.
2. **Instant Truncation & Milliseconds**: All instants in API contracts are epoch milliseconds. Instants are truncated to whole seconds before business calculations per rule R1.
3. **Database Concurrency**:
   - Punch-in concurrency safety is strictly enforced via unique compound index `{emp_code: 1, date: 1}` on `attendance_logs`.
   - Punch-out concurrency safety uses atomic `find_one_and_update({"_id": ..., "punch_out": None}, ...)`.
   - Regularize audit history concurrency utilizes optimistic concurrency matching on the existing `history` array.
4. **Pure Database Aggregations**: All analytics queries (`employee_monthly`, `department_summary`, `late_leaderboard`, `department_trend`) execute entirely within the MongoDB aggregation engine with index support (`IXSCAN`) and zero in-memory Python grouping.

---

## Known Limitations

- Submissions intentionally exclude a Dockerfile per assignment instructions.
- Live analytics pipelines assume a single MongoDB replica set / standalone instance supporting MongoDB 6.0+ window operators (`$densify`, `$setWindowFields`).
