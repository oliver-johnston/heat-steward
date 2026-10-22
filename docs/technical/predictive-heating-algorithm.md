# Predictive-heating algorithm

This page describes the current learning method in technical terms. It is a
robust weighted estimator rather than a linear regression.

## Input data

The learner reads the last 30 days of completed control-cycle samples from the
metrics database. Models are refreshed at most once an hour. Every observation
contains the time, effective room temperature, demand state, boiler state, and
the exact temperature source and entity.

Only observations from the most recently used sensor identity are combined.
This prevents a TRV reading and a differently positioned room sensor from being
treated as one continuous series.

## Warm-up episodes

A warm-up episode contains consecutive samples where room demand is true and the
central-heating relay is confirmed on. The first reading that closes room demand
is included as the endpoint when it follows the episode within 20 minutes. This
captures the final reported rise from sparse sensors instead of assigning it to
the already-satisfied period. A longer gap ends the episode without that endpoint.

An episode becomes a sample only when:

- it lasts from 5 minutes to 4 hours;
- temperature rises by at least `0.1°C`;
- the resulting rate is between `0.05°C/hour` and `5°C/hour`.

The sample value is total temperature rise divided by total episode duration.
At least three accepted episodes are needed.

## Outlier rejection and recent weighting

For warm-up samples, the learner:

1. finds the median;
2. finds the median absolute deviation (MAD);
3. keeps samples within the largest of `3 × MAD`, `0.25`, or 50% of the median;
4. averages the remaining samples with a seven-day recency half-life.

This rejects isolated implausible room responses while allowing a room's normal
rate to change gradually with weather and building conditions. A regression
line is not currently used because the controller needs a stable episode-level
rate, not a detailed curve fitted to every raw point.

## Readiness, staleness, and confidence

The warm-up estimate becomes ready after three samples. It is stale when its
newest sample is more than 14 days old.

The displayed confidence combines evidence, reaching its maximum evidence score
at six samples, with recency using a 14-day half-life. Confidence is a diagnostic
hint; readiness and freshness are the control gates.

## Control use

Preheat duration is the temperature deficit divided by the learned warm-up rate,
plus five minutes for valve movement and cycle timing, limited by the configured
maximum.

See [Predictive heating](../features/predictive-heating.md) for the user-facing
guide.
