# Synthetic Supplementary Dataset

> **Every record in `data/synthetic/` is simulated. These are not real college students.** IDs use the prefixes `SYN-STU-` and `SYN-EV-`, and every row has `Data_Source = SYNTHETIC`.

## Why it exists
The original dataset has 383 registrations and 77 holdout rows, which makes single-split metrics noisy. The synthetic set gives the app a larger, clearly separated dataset for demonstrations, stress-testing bulk prediction and experimenting in the Model Laboratory. It is **not** used for the default model.

## How it was generated
`core/synthetic.py` (generator v1.0) re-implements the rules documented in the original workbook's **Generation_Log, sections 5–10**. The original `generate_ml_dataset.py` was not available. No real record is read or copied. Only aggregate mixes are used (course, year and team counts, and the area-by-team table).

1. **Students:** course, year and team drawn from the documented mixes. Volunteer and UBA participation follow §5. Area of interest by team follows §5. Experience follows the §5 z-rule. Distance comes from three hidden residence groups. Engagement, prior registrations (Poisson plus "very active" students) and prior attendance (Binomial) follow §6.
2. **Events:** type shares 8:7:7:8:6. Popularity = type base + Normal(0, 12), minus 12 for long-format events. Weather forecast is 20:10:6 clear:cloudy:rainy, shuffled. About 25% of events fall near exam weeks.
3. **Registrations:** events chosen with the §6 weights (popularity, area fit, team fit, department fit, long-event penalty). A student never registers for the same event twice. Lead time follows a Gamma distribution with type medians, plus early planners and same-day sign-ups. Exam proximity follows the §6 Bernoulli rule.
4. **Target:** the §9 logistic model with the documented coefficients and a hidden per-student effect, Normal(0, 0.5). The intercept is set by bisection so the mean probability is 68%. The outcome is a Bernoulli draw, so a probability of 0.9 can still produce a no-show.
5. **Missing values, added after the target:** MCAR for distance (5%), popularity (2.9%) and prior registrations (3.9%, with the rate following). MAR for engagement (4.4%, four times as likely for non-volunteers). In addition, **8% of events have no forecast** (`Unavailable`).
6. **Checks (enforced):** no duplicate rows or registrations, no attendance above registrations, no Sunday events, and student and event attributes constant per entity.

### Choices not specified by the documentation
Year mix 80/10/8/2%; volunteer share 0.49; area mix for "Did not apply" equal to the overall survey mix; registrations per student 72/22/4/1.5/0.5%; per-type day, time and duration options; department-to-event-type fit; the engagement stretch of lead time; popularity clipped to 20–95; exam-week share 25%; 8% of events with no forecast. All of these are also listed in the metadata file.

## Reproducibility
`python scripts/generate_synthetic.py --students 600 --events 48 --seed 2026` always produces the same files: 783 registrations, 600 students, 48 events, 528 attend / 255 do not. The seed is intentionally different from the original dataset's seed (42).

## Interpretation
Because the synthetic data follows the same rules as the original simulation, a model trained on one and tested on the other mostly measures rule recovery. The recommended model trained on the original data scores ROC-AUC 0.83 on the synthetic set and predicts 553 attendees where 528 attended (about 5% too high). That is a consistency check, not evidence of real-world performance.
