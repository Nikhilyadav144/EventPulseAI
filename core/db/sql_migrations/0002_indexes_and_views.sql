-- EventPulse AI - migration 0002: indexes for common queries and read-only views
-- used by analytics, prediction and the desktop UI. Adds no columns, drops nothing.

CREATE INDEX ix_registrations_event      ON registrations(event_id);
CREATE INDEX ix_registrations_student    ON registrations(student_id);
CREATE INDEX ix_forecasts_event          ON weather_forecasts(event_id, recorded_utc);
CREATE INDEX ix_predictions_registration ON predictions(registration_id, predicted_utc);
CREATE INDEX ix_predictions_model        ON predictions(model_version_id);
CREATE INDEX ix_predictions_run          ON predictions(run_id);
CREATE INDEX ix_evaluations_event        ON prediction_evaluations(event_id, computed_utc);
CREATE INDEX ix_students_course          ON students(course_id);

-- Most recent forecast for each event (ties broken by forecast_id).
CREATE VIEW v_latest_forecast AS
SELECT f.*
FROM weather_forecasts f
WHERE f.forecast_id = (
    SELECT f2.forecast_id FROM weather_forecasts f2
    WHERE f2.event_id = f.event_id
    ORDER BY f2.recorded_utc DESC, f2.forecast_id DESC LIMIT 1);

-- One row per registration with the 20 model inputs, named as in core/schema.py.
CREATE VIEW v_registration_features AS
SELECT r.registration_id, r.student_id AS Student_ID, r.event_id AS Event_ID, r.status AS Registration_Status,
       c.name AS Course, s.year AS Year,
       s.currently_uba_volunteer AS Currently_UBA_Volunteer,
       s.participated_uba_event_before AS Participated_UBA_Event_Before,
       s.experience_level AS Experience_Level, s.area_of_interest AS Area_Of_Interest, s.role AS Role,
       e.event_type AS Event_Type, e.event_day AS Event_Day, e.time_slot AS Event_Time,
       e.duration_hours AS Event_Duration_Hours, r.registration_days_before AS Registration_Days_Before,
       s.previous_event_attendance AS Previous_Event_Attendance,
       s.previous_events_registered AS Previous_Events_Registered,
       s.previous_attendance_rate AS Previous_Attendance_Rate,
       s.academic_engagement_score AS Academic_Engagement_Score,
       e.popularity AS Event_Popularity, s.distance_from_venue_km AS Distance_From_Venue_KM,
       r.has_exam_near_event AS Has_Exam_Near_Event,
       COALESCE(lf.forecast, 'Unavailable') AS Weather_Forecast,
       lf.forecast_id AS forecast_id, lf.forecast_obtained_at AS forecast_obtained_at,
       c.department AS Department, e.event_date AS Event_Date, e.status AS Event_Status,
       r.data_source AS Data_Source
FROM registrations r
JOIN students s ON s.student_id = r.student_id
JOIN courses  c ON c.course_id  = s.course_id
JOIN events   e ON e.event_id   = r.event_id
LEFT JOIN v_latest_forecast lf ON lf.event_id = e.event_id;

-- Latest prediction per registration and model version.
CREATE VIEW v_latest_predictions AS
SELECT p.*
FROM predictions p
WHERE p.registration_id IS NOT NULL AND p.prediction_id = (
    SELECT p2.prediction_id FROM predictions p2
    WHERE p2.registration_id = p.registration_id AND p2.model_version_id = p.model_version_id
    ORDER BY p2.predicted_utc DESC, p2.prediction_id DESC LIMIT 1);

-- Event-level participation summary (registrations, recorded attendance, latest active-model forecast).
CREATE VIEW v_event_participation AS
SELECT e.event_id, e.name, e.event_type, e.event_date, e.event_day, e.time_slot, e.status, e.data_source,
       COUNT(r.registration_id)                                        AS registrations,
       SUM(CASE WHEN r.status = 'Registered' THEN 1 ELSE 0 END)        AS active_registrations,
       SUM(CASE WHEN a.attended = 1 THEN 1 ELSE 0 END)                 AS recorded_attended,
       SUM(CASE WHEN a.attendance_id IS NOT NULL THEN 1 ELSE 0 END)    AS recorded_outcomes,
       (SELECT ROUND(SUM(lp.probability), 2) FROM v_latest_predictions lp
          JOIN registrations r2 ON r2.registration_id = lp.registration_id
          JOIN model_versions mv ON mv.model_version_id = lp.model_version_id AND mv.is_active = 1
         WHERE r2.event_id = e.event_id AND r2.status = 'Registered') AS expected_attendance_active_model
FROM events e
LEFT JOIN registrations r      ON r.event_id = e.event_id
LEFT JOIN attendance_records a ON a.registration_id = r.registration_id
GROUP BY e.event_id;
