/*
 * Copyright Red Hat
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

import { useEffect, useState } from "react";
import Button from "@material-ui/core/Button";
import Chip from "@material-ui/core/Chip";
import CircularProgress from "@material-ui/core/CircularProgress";
import Dialog from "@material-ui/core/Dialog";
import DialogActions from "@material-ui/core/DialogActions";
import DialogContent from "@material-ui/core/DialogContent";
import DialogTitle from "@material-ui/core/DialogTitle";
import FormControl from "@material-ui/core/FormControl";
import InputLabel from "@material-ui/core/InputLabel";
import MenuItem from "@material-ui/core/MenuItem";
import Select from "@material-ui/core/Select";
import TextField from "@material-ui/core/TextField";
import Typography from "@material-ui/core/Typography";
import { makeStyles } from "@material-ui/core/styles";
import { useApi } from "@backstage/core-plugin-api";
import type {
  ApmeAiProviderSummary,
  ApmeAiProviderConfigureRequest,
  ApmeAiEngineInfo,
} from "../../../common/types";
import { apmeApiRef } from "../../api/ApmeApi";

const useStyles = makeStyles((theme) => ({
  field: {
    marginTop: theme.spacing(2),
    width: "100%",
  },
  helperText: {
    color: theme.palette.text.secondary,
    fontSize: "0.75rem",
    marginTop: theme.spacing(0.5),
  },
  modelRow: {
    display: "flex",
    gap: theme.spacing(1),
    alignItems: "flex-end",
    marginTop: theme.spacing(2),
  },
  modelInput: {
    flex: 1,
  },
  chips: {
    display: "flex",
    flexWrap: "wrap",
    gap: theme.spacing(0.5),
    marginTop: theme.spacing(1),
  },
  stepLabel: {
    color: theme.palette.text.secondary,
    marginBottom: theme.spacing(1),
  },
  errorText: {
    color: theme.palette.error.main,
    marginTop: theme.spacing(1),
  },
}));

export interface ApmeAiProviderDialogProps {
  open: boolean;
  /** Existing provider being edited, or undefined for add. */
  provider?: ApmeAiProviderSummary;
  onClose(): void;
  onSave(
    id: string,
    payload: { configure: ApmeAiProviderConfigureRequest; models: string[] },
  ): Promise<void>;
}

type Step = "setup" | "models";

export const ApmeAiProviderDialog = ({
  open,
  provider,
  onClose,
  onSave,
}: ApmeAiProviderDialogProps) => {
  const classes = useStyles();
  const apmeApi = useApi(apmeApiRef);
  const isEdit = Boolean(provider);

  const [step, setStep] = useState<Step>("setup");
  const [id, setId] = useState(provider?.id ?? "");
  const [engine, setEngine] = useState(provider?.engine ?? "");
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [modelInput, setModelInput] = useState("");
  const [models, setModels] = useState<string[]>(provider?.models ?? []);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | undefined>();

  const [enginesLoading, setEnginesLoading] = useState(false);
  const [enginesError, setEnginesError] = useState<string | undefined>();
  const [engines, setEngines] = useState<ApmeAiEngineInfo[]>([]);

  useEffect(() => {
    if (!open) {
      return;
    }
    setEnginesLoading(true);
    setEnginesError(undefined);
    apmeApi
      .getAiEngines()
      .then((res) => {
        const list = (res.engines ?? []).filter((e) => e.id !== "mock");
        setEngines(list);
        setEngine((prev) => {
          if (prev) {
            return prev;
          }
          return list.length > 0 ? list[0].id : "";
        });
      })
      .catch((err) => {
        setEnginesError(
          err instanceof Error ? err.message : "Failed to load engines",
        );
      })
      .finally(() => setEnginesLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const handleClose = () => {
    setStep("setup");
    setId(provider?.id ?? "");
    setEngine(provider?.engine ?? "");
    setApiKey("");
    setBaseUrl("");
    setModelInput("");
    setModels(provider?.models ?? []);
    setSaving(false);
    setError(undefined);
    onClose();
  };

  const handleAddModel = () => {
    const trimmed = modelInput.trim();
    if (trimmed && !models.includes(trimmed)) {
      setModels((prev) => [...prev, trimmed]);
    }
    setModelInput("");
  };

  const handleRemoveModel = (model: string) => {
    setModels((prev) => prev.filter((m) => m !== model));
  };

  const handleSave = async () => {
    setSaving(true);
    setError(undefined);
    try {
      // Abbenay SoT via Gateway proxy (ADR-070 / apme#561): one-shot configure
      // with apiKey + secretStore:file writes secrets.json on the Abbenay volume.
      const configure: ApmeAiProviderConfigureRequest = {
        engine,
        secretStore: "file",
      };
      const trimmedKey = apiKey.trim();
      if (trimmedKey) {
        configure.apiKey = trimmedKey;
        const secretName = engines.find((e) => e.id === engine)?.defaultEnvVar;
        if (secretName) {
          configure.secretName = secretName;
        }
      }
      const trimmedUrl = baseUrl.trim();
      if (trimmedUrl) {
        configure.baseUrl = trimmedUrl;
      }
      await onSave(isEdit ? provider!.id : id.trim(), { configure, models });
      handleClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  const needsKey = Boolean(engines.find((e) => e.id === engine)?.requiresKey);
  const canAdvance = isEdit
    ? Boolean(engine)
    : Boolean(id.trim()) &&
      Boolean(engine) &&
      (!needsKey || Boolean(apiKey.trim()));

  // When editing a provider whose engine is not in the live list, keep it visible.
  const engineIds = engines.map((e) => e.id);
  const engineOptions: string[] = (
    engine && !engineIds.includes(engine) ? [...engineIds, engine] : engineIds
  ).filter(Boolean);

  const selectedEngineInfo = engines.find((e) => e.id === engine);

  let apiKeyHelper =
    "Stored in Abbenay's file secret store (secrets.json on the config volume). The APME service proxies the key and does not keep it.";
  if (isEdit) {
    apiKeyHelper = `Leave blank to keep the existing key. ${apiKeyHelper}`;
  } else if (needsKey) {
    apiKeyHelper = `Required for ${engine || "this engine"}. ${apiKeyHelper}`;
  }

  const title = isEdit ? `Edit provider: ${provider!.id}` : "Add AI provider";

  return (
    <Dialog
      maxWidth="sm"
      fullWidth
      open={open}
      aria-labelledby="apme-ai-provider-dialog-title"
    >
      <DialogTitle id="apme-ai-provider-dialog-title">{title}</DialogTitle>
      <DialogContent dividers>
        {step === "setup" && (
          <>
            <Typography variant="body2" className={classes.stepLabel}>
              Step 1 of 2 — Provider setup
            </Typography>

            {!isEdit && (
              <>
                <TextField
                  className={classes.field}
                  label="Provider name"
                  variant="outlined"
                  size="small"
                  value={id}
                  onChange={(e) => setId(e.target.value)}
                  disabled={saving}
                  required
                  inputProps={{ "aria-label": "Provider name" }}
                  placeholder="my-openai"
                />
                <Typography className={classes.helperText}>
                  Abbenay provider key (lowercase letters, digits, hyphens).
                </Typography>
              </>
            )}

            {enginesLoading && (
              <div
                className={classes.field}
                style={{ display: "flex", alignItems: "center", gap: 8 }}
              >
                <CircularProgress size={16} />
                <Typography variant="body2" className={classes.helperText}>
                  Loading engines…
                </Typography>
              </div>
            )}

            {enginesError && (
              <Typography variant="body2" className={classes.errorText}>
                {enginesError}
              </Typography>
            )}

            {!enginesLoading && !enginesError && (
              <FormControl
                className={classes.field}
                variant="outlined"
                size="small"
                required
              >
                <InputLabel id="apme-engine-label">Engine</InputLabel>
                <Select
                  labelId="apme-engine-label"
                  label="Engine"
                  value={engine}
                  onChange={(e) => setEngine(e.target.value as string)}
                  disabled={saving || engineOptions.length === 0}
                >
                  {engineOptions.map((opt) => (
                    <MenuItem key={opt} value={opt}>
                      {opt}
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>
            )}

            <TextField
              className={classes.field}
              label="API key"
              variant="outlined"
              size="small"
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              disabled={saving}
              required={needsKey && !isEdit}
              inputProps={{ "aria-label": "API key" }}
              autoComplete="new-password"
            />
            <Typography className={classes.helperText}>
              {apiKeyHelper}
            </Typography>

            <TextField
              className={classes.field}
              label="Base URL (optional)"
              variant="outlined"
              size="small"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              disabled={saving}
              inputProps={{ "aria-label": "Base URL" }}
              placeholder={selectedEngineInfo?.defaultBaseUrl}
            />
            <Typography className={classes.helperText}>
              API root only (e.g. https://host/v1). Do not append
              /chat/completions — Abbenay adds the path it needs.
            </Typography>
          </>
        )}

        {step === "models" && (
          <>
            <Typography variant="body2" className={classes.stepLabel}>
              Step 2 of 2 — Models
            </Typography>

            <div className={classes.modelRow}>
              <TextField
                className={classes.modelInput}
                label="Model ID"
                variant="outlined"
                size="small"
                value={modelInput}
                onChange={(e) => setModelInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    handleAddModel();
                  }
                }}
                disabled={saving}
                inputProps={{ "aria-label": "Model ID" }}
              />
              <Button
                variant="outlined"
                color="primary"
                onClick={handleAddModel}
                disabled={saving || !modelInput.trim()}
              >
                Add
              </Button>
            </div>

            {models.length > 0 && (
              <ul
                className={classes.chips}
                aria-label="Enabled models"
                style={{ listStyle: "none", padding: 0, margin: 0 }}
              >
                {models.map((model) => (
                  <li key={model} style={{ display: "inline-block" }}>
                    <Chip
                      label={model}
                      onDelete={() => handleRemoveModel(model)}
                      size="small"
                    />
                  </li>
                ))}
              </ul>
            )}
            {models.length === 0 && (
              <Typography variant="body2" className={classes.helperText}>
                No models added yet. Add at least one model ID.
              </Typography>
            )}
          </>
        )}

        {error && (
          <Typography
            variant="body2"
            className={classes.errorText}
            role="alert"
          >
            {error}
          </Typography>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={handleClose} color="primary" disabled={saving}>
          Cancel
        </Button>
        {step === "setup" && (
          <Button
            color="primary"
            variant="contained"
            onClick={() => setStep("models")}
            disabled={!canAdvance || saving}
          >
            Next: Models
          </Button>
        )}
        {step === "models" && (
          <>
            <Button
              color="primary"
              onClick={() => setStep("setup")}
              disabled={saving}
            >
              Back
            </Button>
            <Button
              color="primary"
              variant="contained"
              onClick={() => void handleSave()}
              disabled={saving}
            >
              {saving ? "Saving…" : "Save"}
            </Button>
          </>
        )}
      </DialogActions>
    </Dialog>
  );
};
