import { MdFilledButton, MdOutlinedButton } from "../material";
import { DiagnosticsHistory } from "../DiagnosticsHistory";
import { WarningIcon } from "../components/AppChrome";
import {
  OverrideClockGraphic,
  appliedOverrideForRoom,
  diagnosticStatusLabel,
  formatAge,
  formatBytes,
  formatDateTime,
  formatNextScheduleValue,
  formatTemperature,
  overrideActorLabel,
  overrideScopeLabel,
  overrideSummary,
  relayState,
} from "../components/HeatingCards";
import { ROOM_STATUS_LABELS, scheduleValueName } from "../uiHelpers";
import type { DiagnosticsData, TemperaturePresets } from "../types";

type Props = {
  loading: boolean;
  diagnostics: DiagnosticsData | null;
  dashboardPresets: TemperaturePresets;
  acknowledgingFault: boolean;
  onReload: () => void;
  onAcknowledgeFault: () => void;
};

export function DiagnosticsPage({
  loading: diagnosticsLoading,
  diagnostics,
  dashboardPresets,
  acknowledgingFault,
  onReload,
  onAcknowledgeFault,
}: Props) {
  return (
        <section className="diagnostics" aria-live="polite">
          {diagnosticsLoading && !diagnostics ? (
            <div className="loading">Loading system diagnostics…</div>
          ) : diagnostics ? (
            <div className="diagnostics-content">
              <section className={`diagnostic-overview diagnostic-${diagnostics.overall_status}`}>
                <div>
                  <span className="diagnostic-kicker">System status</span>
                  <strong>{diagnosticStatusLabel(diagnostics.overall_status)}</strong>
                </div>
                <span>
                  Updated {formatDateTime(diagnostics.updated_at)}
                </span>
              </section>

              {diagnostics.controller.fault_latched && (
                <section className="fault-recovery" role="alert">
                  <WarningIcon />
                  <div>
                    <strong>Heating is paused by a safety stop</strong>
                    <p>{diagnostics.controller.fault_reason}</p>
                    <small>
                      Check the affected device or relay first. Acknowledging clears the fault but
                      does not turn automatic control back on.
                    </small>
                  </div>
                  <MdOutlinedButton
                    type="button"
                    disabled={acknowledgingFault}
                    onClick={() => onAcknowledgeFault()}
                  >
                    {acknowledgingFault ? "Acknowledging…" : "Acknowledge fault"}
                  </MdOutlinedButton>
                </section>
              )}

              <div className="diagnostic-summary-grid">
                <article className="diagnostic-summary-card">
                  <span>Heating mode</span>
                  <strong>{diagnostics.controller.mode}</strong>
                  <small>
                    {diagnostics.controller.active_boosts.length
                      ? `${diagnostics.controller.active_boosts.length} active boost${diagnostics.controller.active_boosts.length === 1 ? "" : "s"}`
                      : "No active boosts"}
                  </small>
                </article>
                <article className="diagnostic-summary-card">
                  <span>Control loop</span>
                  <strong>{diagnostics.controller.loop_status.replace("_", " ")}</strong>
                  <small>
                    Last successful cycle: {formatDateTime(diagnostics.controller.last_successful_cycle_at)}
                  </small>
                  <small>Expected every {diagnostics.controller.cycle_interval_seconds} seconds</small>
                  {diagnostics.controller.consecutive_failures > 0 && (
                    <small>
                      Consecutive failed cycles: {diagnostics.controller.consecutive_failures}
                    </small>
                  )}
                  {diagnostics.controller.loop_restarts > 0 && (
                    <small>Recovered loop crashes: {diagnostics.controller.loop_restarts}</small>
                  )}
                </article>
                <article className="diagnostic-summary-card">
                  <span>Central heating</span>
                  <strong>{relayState(diagnostics.plant.boiler.actual, diagnostics.plant.boiler.configured)}</strong>
                  <small>
                    Requested: {relayState(diagnostics.plant.boiler.requested, diagnostics.plant.boiler.configured)}
                  </small>
                  {diagnostics.plant.boiler.entity_id && <code>{diagnostics.plant.boiler.entity_id}</code>}
                </article>
                <article className="diagnostic-summary-card">
                  <span>Hot water</span>
                  <strong>{relayState(diagnostics.plant.hot_water.actual, diagnostics.plant.hot_water.configured)}</strong>
                  <small>
                    Requested: {relayState(diagnostics.plant.hot_water.requested, diagnostics.plant.hot_water.configured)}
                  </small>
                  {diagnostics.plant.hot_water.entity_id && <code>{diagnostics.plant.hot_water.entity_id}</code>}
                </article>
                {diagnostics.metrics && (
                  <article className="diagnostic-summary-card">
                    <span>Historical metrics</span>
                    <strong>{diagnostics.metrics.healthy ? "Recording" : "Problem"}</strong>
                    <small>
                      {diagnostics.metrics.room_sample_count.toLocaleString()} room samples ·{" "}
                      {formatBytes(diagnostics.metrics.database_size_bytes)}
                    </small>
                    <small>
                      Last recorded: {formatDateTime(diagnostics.metrics.last_successful_write_at)}
                    </small>
                    <small>{diagnostics.metrics.retention_days} days of raw history retained</small>
                  </article>
                )}
              </div>

              <DiagnosticsHistory
                rooms={diagnostics.rooms.map((room) => ({
                  device_id: room.device_id,
                  name: room.name,
                  room_group: room.room_group,
                  climate_entity: room.climate_entity,
                }))}
                refreshKey={diagnostics.updated_at}
              />

              <section className="diagnostic-section">
                <header className="diagnostic-section-heading">
                  <h2 className="md-typescale-headline-small">Issues</h2>
                  <span>{diagnostics.issues.length}</span>
                </header>
                {diagnostics.issues.length ? (
                  <div className="diagnostic-issues">
                    {diagnostics.issues.map((issue, index) => (
                      <article className={`diagnostic-issue ${issue.severity}`} key={`${issue.title}-${index}`}>
                        <WarningIcon />
                        <div>
                          <strong>{issue.title}</strong>
                          <p>{issue.detail}</p>
                        </div>
                      </article>
                    ))}
                  </div>
                ) : (
                  <p className="diagnostic-empty">No current issues.</p>
                )}
              </section>

              <section className="diagnostic-section">
                <header className="diagnostic-section-heading">
                  <h2 className="md-typescale-headline-small">Current overrides</h2>
                  <span>
                    {diagnostics.controller.active_temperature_overrides.length +
                      (diagnostics.controller.active_hot_water_override ? 1 : 0)}
                  </span>
                </header>
                {diagnostics.controller.active_temperature_overrides.length > 0 ||
                diagnostics.controller.active_hot_water_override ? (
                  <div className="diagnostic-override-list">
                    {[
                      ...diagnostics.controller.active_temperature_overrides,
                      ...(diagnostics.controller.active_hot_water_override
                        ? [diagnostics.controller.active_hot_water_override]
                        : []),
                    ]
                      .sort((left, right) => right.sequence - left.sequence)
                      .map((override) => (
                        <article className="diagnostic-override" key={override.action_id}>
                          <div className="diagnostic-override-heading">
                            <span className="diagnostic-override-icon" aria-hidden="true">
                              <OverrideClockGraphic />
                            </span>
                            <div>
                              <strong>{overrideScopeLabel(override, diagnostics.rooms)}</strong>
                              <span>
                                {"on" in override ? "Hot water" : `${scheduleValueName(override.scope_type)} temperature`}
                              </span>
                            </div>
                          </div>
                          <strong>{overrideSummary(override, dashboardPresets)}</strong>
                          <dl>
                            <div>
                              <dt>Started</dt>
                              <dd>{formatDateTime(override.started_at)}</dd>
                            </div>
                            <div>
                              <dt>Initiated by</dt>
                              <dd>{overrideActorLabel(override)}</dd>
                            </div>
                          </dl>
                        </article>
                      ))}
                  </div>
                ) : (
                  <p className="diagnostic-empty">No temporary overrides are active.</p>
                )}
              </section>

              <section className="diagnostic-section">
                <header className="diagnostic-section-heading">
                  <h2 className="md-typescale-headline-small">Room decisions</h2>
                  <span>{diagnostics.rooms.length}</span>
                </header>
                <div className="diagnostic-room-list">
                  {diagnostics.rooms.map((room) => {
                    const appliedOverride = appliedOverrideForRoom(
                      room,
                      diagnostics.controller.active_temperature_overrides,
                    );
                    return <article className="diagnostic-room" key={room.device_id}>
                      <header>
                        <div>
                          <strong>{room.name}</strong>
                          <span>{room.room_group}</span>
                        </div>
                        <span className={`room-status-badge room-status-${room.status}`}>
                          {ROOM_STATUS_LABELS[room.status]}
                        </span>
                      </header>
                      <dl>
                        <div>
                          <dt>Measured</dt>
                          <dd>{formatTemperature(room.current_temperature, "°C")}</dd>
                        </div>
                        <div>
                          <dt>Effective target</dt>
                          <dd>{scheduleValueName(room.preset)} · {formatTemperature(room.scheduled_temperature, "°C")}</dd>
                        </div>
                        <div>
                          <dt>Demand</dt>
                          <dd>{room.demand ? "Requesting heat" : "Satisfied"}</dd>
                        </div>
                        <div>
                          <dt>TRV command</dt>
                          <dd>{formatTemperature(room.commanded_temperature, "°C")}</dd>
                        </div>
                      </dl>

                      <div className="diagnostic-reasoning" aria-label="Control decision explanation">
                        <div>
                          <strong>Target</strong>
                          <span>
                            {room.temperature_reason ??
                              `${scheduleValueName(room.control_source)} sets the effective target to ${formatTemperature(room.scheduled_temperature, "°C")}.`}
                          </span>
                        </div>
                        <div>
                          <strong>Demand</strong>
                          <span>
                            {room.demand_reason ??
                              (room.demand
                                ? "The room is currently requesting heat."
                                : "The room is currently satisfied.")}
                          </span>
                        </div>
                        <div>
                          <strong>Command</strong>
                          <span>
                            {room.command_reason ??
                              `The controller selected a ${formatTemperature(room.commanded_temperature, "°C")} TRV command.`}
                          </span>
                        </div>
                      </div>

                      {appliedOverride && (
                        <div className="diagnostic-applied-override">
                          <span className="diagnostic-override-icon" aria-hidden="true">
                            <OverrideClockGraphic />
                          </span>
                          <div>
                            <strong>
                              {scheduleValueName(appliedOverride.scope_type)} override applied
                            </strong>
                            <span>
                              {overrideSummary(appliedOverride, dashboardPresets)} · started {formatDateTime(appliedOverride.started_at)} · {overrideActorLabel(appliedOverride)}
                            </span>
                          </div>
                        </div>
                      )}

                      {room.warning && (
                        <div className="diagnostic-room-warning">
                          <WarningIcon />
                          <span>{room.warning}</span>
                        </div>
                      )}

                      <dl className="diagnostic-room-facts">
                        <div>
                          <dt>Room schedule</dt>
                          <dd>
                            {scheduleValueName(room.schedule_preset ?? room.preset)} · {formatTemperature(
                              room.schedule_temperature ?? room.scheduled_temperature,
                              "°C",
                            )}
                          </dd>
                        </div>
                        <div>
                          <dt>Control source</dt>
                          <dd>{scheduleValueName(room.control_source)}</dd>
                        </div>
                        <div>
                          <dt>Predictive model</dt>
                          <dd>
                            {scheduleValueName(room.thermal_model_status)}
                            {room.predictive_action
                              ? ` · ${scheduleValueName(room.predictive_action)}`
                              : ""}
                          </dd>
                        </div>
                        <div>
                          <dt>Learned heating rate</dt>
                          <dd>
                            {room.heating_rate_c_per_hour === null
                              ? `Learning · ${room.heating_samples} episodes`
                              : `${room.heating_rate_c_per_hour.toFixed(2)}°C/hour · ${room.heating_samples} episodes`}
                          </dd>
                        </div>
                        <div>
                          <dt>Prediction</dt>
                          <dd>
                            {room.predicted_minutes_to_target === null
                              ? `${Math.round(room.model_confidence * 100)}% confidence`
                              : `${Math.round(room.predicted_minutes_to_target)} minutes to target · ${Math.round(room.model_confidence * 100)}% confidence`}
                          </dd>
                        </div>
                        <div>
                          <dt>Next schedule</dt>
                          <dd>{formatNextScheduleValue(room)}</dd>
                        </div>
                        <div>
                          <dt>Temperature source</dt>
                          <dd>{room.temperature_source}</dd>
                        </div>
                        <div>
                          <dt>Temperature entity</dt>
                          <dd><code>{room.temperature_entity}</code></dd>
                        </div>
                        <div>
                          <dt>External sensor</dt>
                          <dd>{formatTemperature(room.external_temperature, "°C")}</dd>
                        </div>
                        <div>
                          <dt>TRV sensor</dt>
                          <dd>{formatTemperature(room.trv_temperature, "°C")}</dd>
                        </div>
                        <div>
                          <dt>Sensor health</dt>
                          <dd>
                            {room.external_temperature_freshness === "disabled"
                              ? "Freshness check disabled"
                              : room.external_temperature_health_entity
                                ? `${formatAge(room.external_temperature_age_seconds)} via ${room.external_temperature_health_entity}`
                                : "Not configured"}
                          </dd>
                        </div>
                        <div>
                          <dt>TRV last seen</dt>
                          <dd>{room.trv_last_seen_at ? formatDateTime(room.trv_last_seen_at) : "Unavailable"}</dd>
                        </div>
                        <div>
                          <dt>Reported target</dt>
                          <dd>{formatTemperature(room.reported_target_temperature, "°C")}</dd>
                        </div>
                        <div>
                          <dt>TRV mode / action</dt>
                          <dd>{room.hvac_mode ?? "unknown"} / {room.hvac_action ?? "unknown"}</dd>
                        </div>
                        <div>
                          <dt>Valve opening</dt>
                          <dd>{room.valve_opening_degree == null ? "Unavailable" : `${room.valve_opening_degree}%`}</dd>
                        </div>
                        <div>
                          <dt>Valve closing threshold</dt>
                          <dd>{room.valve_closing_degree == null ? "Unavailable" : `${room.valve_closing_degree}%`}</dd>
                        </div>
                        <div>
                          <dt>Battery / link quality</dt>
                          <dd>
                            {room.battery_level == null ? "Unavailable" : `${room.battery_level}%`} /{" "}
                            {room.linkquality == null ? "Unavailable" : room.linkquality}
                          </dd>
                        </div>
                        <div>
                          <dt>Climate entity</dt>
                          <dd><code>{room.climate_entity}</code></dd>
                        </div>
                      </dl>
                    </article>;
                  })}
                </div>
              </section>

              <section className="diagnostic-section">
                <header className="diagnostic-section-heading">
                  <h2 className="md-typescale-headline-small">Recent activity</h2>
                  <span>{diagnostics.events.length}</span>
                </header>
                {diagnostics.events.length ? (
                  <ol className="diagnostic-events">
                    {diagnostics.events.map((event, index) => (
                      <li className={`diagnostic-event ${event.level}`} key={`${event.occurred_at}-${index}`}>
                        <time dateTime={event.occurred_at}>{formatDateTime(event.occurred_at)}</time>
                        <span>{event.message}</span>
                      </li>
                    ))}
                  </ol>
                ) : (
                  <p className="diagnostic-empty">No control activity has been recorded yet.</p>
                )}
              </section>
            </div>
          ) : (
            <div className="empty-state">
              <h2 className="md-typescale-headline-small">Diagnostics are unavailable</h2>
              <MdFilledButton type="button" onClick={() => onReload()}>Try again</MdFilledButton>
            </div>
          )}
        </section>
  );
}
