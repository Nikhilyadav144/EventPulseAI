# EventPulse AI Prediction API

## Status and scope

The FastAPI service is a genuine trained-model prediction API. It is not yet the operational event-management web backend. It loads `model/model.joblib` and its metadata once during FastAPI lifespan startup.

All predictions and metrics carry the simulated-target limitation: the model was trained against simulated `Will_Attend` labels, not observed real attendance.

## Run locally

```bash
.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

Then use `http://127.0.0.1:8000/` for the form and `http://127.0.0.1:8000/docs` for interactive OpenAPI documentation.

## Endpoints

| Method and path | Result |
|---|---|
| `GET /` | Static browser prediction interface. |
| `GET /health` | Service/model readiness, model name and app version. |
| `POST /predict` | One validated pre-event registration to attendance probability, classification, tips and sensitivity factors. |
| `GET /model-info` | Model identity, evaluation summary and browser-form field descriptions. |
| `GET /monitoring/summary` | Counts local accepted/rejected prediction logs. |
| `GET /docs` | FastAPI Swagger UI. |

## Prediction request

`POST /predict` accepts JSON. Required fields are all declared fields except optional numeric inputs and `Weather_Forecast`, which defaults to `Unavailable`. The API rejects unknown fields.

```json
{
  "Course": "B.Tech CSE",
  "Year": 2,
  "Currently_UBA_Volunteer": 1,
  "Participated_UBA_Event_Before": 1,
  "Experience_Level": "Some experience",
  "Area_Of_Interest": "Problem Solving & Decision Making",
  "Role": "Content",
  "Event_Type": "Workshop",
  "Event_Day": "Wednesday",
  "Event_Time": "Morning",
  "Event_Duration_Hours": 2.0,
  "Registration_Days_Before": 5,
  "Previous_Event_Attendance": 4,
  "Previous_Events_Registered": 5,
  "Previous_Attendance_Rate": 80.0,
  "Academic_Engagement_Score": 72.0,
  "Event_Popularity": 70.0,
  "Distance_From_Venue_KM": 3.5,
  "Has_Exam_Near_Event": 0,
  "Weather_Forecast": "Clear"
}
```

The model enforces categorical enums, numeric bounds, strict integer count fields, finite numeric values and history consistency: prior attendance cannot exceed prior registrations, and a supplied prior-attendance rate must agree with the counts. Invalid requests return HTTP 422. Non-finite inputs such as JSON `NaN` are rejected and produce JSON-safe 422 error responses.

## Prediction response

```json
{
  "will_attend": true,
  "probability": 0.9867,
  "threshold": 0.72,
  "certainty": "Clear",
  "label": "Will attend",
  "tips": ["..."],
  "factors": [{"feature": "...", "label": "...", "value": "...", "typical": "...", "effect": 0.0}],
  "model": "Logistic Regression (end-term, MLflow)",
  "notice": "Will_Attend in this dataset is SIMULATED ..."
}
```

`probability` comes from the serialized sklearn pipeline; `will_attend` is `probability >= threshold`. Sensitivity factors replace one input at a time with its training reference value and are not causal explanations.

## Operational notes

Accepted request inputs are logged locally to `logs/predictions.jsonl`; rejected request field names/types are logged to `logs/rejected.jsonl`. These filesystem logs are for local monitoring only and must be replaced by durable managed logging before hosted deployment. The current API does not expose event, registration, attendance, QR, report or authentication endpoints.


## Verification (16 November 2026, Mac, server `uvicorn app:app` on 127.0.0.1:8000)

Logs: `logs/backend_verify/` (`smoke.txt`, `requests.txt`, `uvicorn.log`). Test logs were written to
`logs/backend_verify/predictions_test.jsonl` so the real `logs/predictions.jsonl` was not touched.

| Request | Result |
|---|---|
| `GET /health` | 200, `model_loaded: true`, model "Logistic Regression (end-term, MLflow)", version 1.2.0 |
| Startup | model loaded once (one "Application startup complete") |
| `POST /predict` valid example | 200, probability 0.9867, "Will attend", 2 tips |
| 25 registrations taken from the dataset | 25/25 → 200 |
| Missing `Course` | 422 |
| `Year: "two"`, `Event_Duration_Hours: "2h"`, `Year: 2.5` | 422 each |
| `Year: 9`, distance -1, unknown event type, attended > registered | 422 each |
| `NaN` duration | 422 with JSON-safe body (`"input": "nan"`) |
| Unknown extra field, non-JSON body, empty body | 422 each |
| `GET /`, `GET /docs`, `GET /openapi.json` | 200 each |
| Logging | 27 accepted predictions logged, 15 rejected requests logged (field + error type only) |
| Browser UI (Claude's built-in browser) | form built from `/model-info`; weak example → "<0.1 %, Likely no-show" with 3 tips; Year = 9 → "Input rejected (HTTP 422)" shown per field |
