import type { FormEventHandler } from "react";
import { MdFilledButton, MdOutlinedTextField, MdSwitch, MdTextButton } from "../../material";
import { ControlSettingRow, TechnicalPill } from "../../components/HeatingCards";
import { PREHEATING_CONTROL_FIELDS } from "../../configForms";
import { eventSelected, eventValue } from "../../uiHelpers";
import type { ControlValues, Discovery, Selection } from "../../types";

const REQUIRED_EPISODES = 3;

function episodeEvidence(samples: number) {
  return samples >= REQUIRED_EPISODES
    ? `${samples} recorded`
    : `${samples} / ${REQUIRED_EPISODES}`;
}

type Props = {
  discovery: Discovery;
  selections: Record<string, Selection>;
  updateSelection: (deviceId: string, patch: Partial<Selection>) => void;
  control: ControlValues;
  updateControl: (patch: Partial<ControlValues>) => void;
  saving: boolean;
  notice: string;
  visibleError: string;
  onSubmit: FormEventHandler<HTMLFormElement>;
  onRevert: () => void;
};

export function PreheatingSettingsPage({
  discovery,
  selections,
  updateSelection,
  control,
  updateControl,
  saving,
  notice,
  visibleError,
  onSubmit,
  onRevert,
}: Props) {
  const rooms = discovery.trvs.filter((trv) => selections[trv.device_id]?.enabled);

  return (
    <form onSubmit={onSubmit}>
      <section className="section-heading">
        <div>
          <h2 className="md-typescale-headline-small">Preheating</h2>
          <p>Use each room’s learned response to arrive at scheduled temperatures on time.</p>
        </div>
      </section>

      <section className="settings-list preheating-settings">
        <section className="control-settings-group">
          <header className="control-settings-heading">
            <h3 className="md-typescale-title-large">Predictive control</h3>
            <p>The master switch applies to every room enabled below.</p>
          </header>
          <div className="control-fields">
            <ControlSettingRow
              title="Predictive heating"
              description="Start rooms early for schedule increases using each room’s learned warm-up rate."
            >
              <MdSwitch
                aria-label="Predictive heating"
                selected={control.predictive_heating_enabled}
                onChange={(event) =>
                  updateControl({ predictive_heating_enabled: eventSelected(event) })
                }
              />
            </ControlSettingRow>
            {PREHEATING_CONTROL_FIELDS.map((field) => (
              <ControlSettingRow
                title={field.title}
                description={field.description}
                key={field.key}
              >
                <MdOutlinedTextField
                  type="number"
                  min={field.min}
                  max={field.max}
                  step={field.step}
                  label={field.inputLabel}
                  value={control[field.key]}
                  onInput={(event) => updateControl({ [field.key]: eventValue(event) })}
                />
              </ControlSettingRow>
            ))}
          </div>
        </section>

        <section className="control-settings-group">
          <header className="control-settings-heading">
            <h3 className="md-typescale-title-large">Rooms</h3>
            <p>
              A room becomes available after three recent warm-up episodes using its selected
              temperature sensor.
            </p>
          </header>
          <div className="preheating-room-grid">
            {rooms.map((trv) => {
              const selection = selections[trv.device_id];
              const model = discovery.predictive_models?.[trv.device_id];
              const selectedTemperatureEntity =
                selection.external_temperature_sensor || trv.entities.climate;
              const matchesSensor = model?.temperature_entity === selectedTemperatureEntity;
              const ready = Boolean(model?.ready && matchesSensor);
              const showSwitch = ready || selection.predictive_heating_enabled;
              const currentModel = matchesSensor ? model : undefined;
              const minutesPerDegree = currentModel?.heating_rate_c_per_hour
                ? Math.round(60 / currentModel.heating_rate_c_per_hour)
                : null;
              return (
                <article className={`preheating-room-card ${ready ? "ready" : "learning"}`} key={trv.device_id}>
                  <header>
                    <strong>{trv.area_name || trv.name}</strong>
                    {showSwitch ? (
                      <MdSwitch
                        aria-label={`Predictive heating for ${trv.area_name || trv.name}`}
                        selected={selection.predictive_heating_enabled}
                        onChange={(event) =>
                          updateSelection(trv.device_id, {
                            predictive_heating_enabled: eventSelected(event),
                          })
                        }
                      />
                    ) : (
                      <span className="preheating-disabled-status">
                        <TechnicalPill label="Disabled" value="Learning" />
                      </span>
                    )}
                  </header>
                  {discovery.predictive_models_error ? (
                    <p>{discovery.predictive_models_error}</p>
                  ) : (
                    <div className="preheating-room-metrics">
                      {model && !matchesSensor && (
                        <TechnicalPill label="Model" value="Restarting" />
                      )}
                      {selection.predictive_heating_enabled && !ready && matchesSensor && (
                        <TechnicalPill
                          label="Model"
                          value={currentModel?.status === "stale" ? "Stale" : "Learning"}
                        />
                      )}
                      <TechnicalPill
                        label="Warm-up episodes"
                        value={episodeEvidence(currentModel?.heating_samples ?? 0)}
                      />
                      {currentModel?.heating_rate_c_per_hour != null && (
                        <TechnicalPill
                          label="Warm-up rate"
                          value={`${currentModel.heating_rate_c_per_hour.toFixed(2)}°C/hour`}
                        />
                      )}
                      {minutesPerDegree != null && (
                        <TechnicalPill
                          label="Preheat estimate"
                          value={`~${minutesPerDegree} min/°C`}
                        />
                      )}
                    </div>
                  )}
                </article>
              );
            })}
          </div>
        </section>
      </section>

      <footer className="save-bar">
        <div className="save-actions">
          <MdTextButton type="button" disabled={saving} onClick={onRevert}>
            Revert
          </MdTextButton>
          <MdFilledButton type="submit" disabled={saving}>
            {saving ? "Saving…" : "Save preheating settings"}
          </MdFilledButton>
        </div>
      </footer>
      {notice && <div className="message success bottom-notice" role="status">{notice}</div>}
      {visibleError && (
        <div className="message error bottom-message" role="alert">
          <strong>Couldn’t continue.</strong> {visibleError}
        </div>
      )}
    </form>
  );
}
